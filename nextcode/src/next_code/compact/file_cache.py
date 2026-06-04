"""FileStateCache — track file read state for edit safety and post-compact rebuild.

Core semantics:
- is_partial_view: files read with offset/limit are flagged as partial;
  editing a partially-viewed file is blocked because unseen content may be damaged.
  However, if the file was previously fully read and hasn't changed on disk (mtime
  unchanged), a subsequent offset/limit read does NOT downgrade to partial — we
  already have the full content cached.
- get_cached_content: returns cached content when the file hasn't changed on disk
  (mtime match), avoiding redundant disk I/O.
- get_recent(): returns most recently accessed files, used to re-inject
  file contents after Full Compact so the model doesn't "lose memory".
- LRU eviction with dual limits (max entries + max size).
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path


# Skip caching files larger than this to avoid excessive memory use
_MAX_CACHEABLE_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


@dataclass
class FileStateEntry:
    """A single file's read/edit state in the cache."""

    path: str
    content: str  # Full file content at read/edit time (for diff and rebuild)
    timestamp: float  # time.monotonic()
    mtime: float = 0.0  # st_mtime from disk — used to detect file changes
    offset: int | None = None  # Read offset parameter
    limit: int | None = None  # Read limit parameter
    is_partial_view: bool = False  # True if only part of the file was read
    was_edited: bool = False  # True if the file was edited/written since last read


class FileStateCache:
    """LRU cache tracking file read/edit state.

    Three roles:
    1. Content cache: avoid re-reading unchanged files from disk (get_cached_content).
    2. Safety guard: block edits to partially-viewed files (can_edit).
    3. Rebuild index: provide recently-accessed files for post-compact
       re-injection (get_recent).
    """

    def __init__(
        self,
        max_entries: int = 100,
        max_size_bytes: int = 25 * 1024 * 1024,  # 25 MB
    ) -> None:
        self._entries: OrderedDict[str, FileStateEntry] = OrderedDict()
        self._max_entries = max_entries
        self._max_size_bytes = max_size_bytes
        self._current_size_bytes: int = 0

    # ── Content Cache ──────────────────────────────────────────────────

    def get_cached_content(self, path: str, mtime: float) -> str | None:
        """Return cached content if the file hasn't changed on disk.

        Conditions for a cache hit:
        - File is in the cache
        - Cached entry is NOT a partial view (we have the full content)
        - File mtime matches (file hasn't been modified externally)

        Returns None on cache miss (caller should read from disk).
        """
        entry = self._entries.get(path)
        if entry is None:
            return None
        if entry.is_partial_view:
            return None
        if mtime != entry.mtime:
            return None
        return entry.content

    # ── Recording ──────────────────────────────────────────────────────

    def record_read(
        self,
        path: str,
        content: str,
        offset: int | None = None,
        limit: int | None = None,
        mtime: float = 0.0,
    ) -> None:
        """Record that a file was read.

        Args:
            path: Absolute file path.
            content: Full file content (even if offset/limit was used for display).
            offset: Read offset parameter (None = from start).
            limit: Read limit parameter (None = to end).
            mtime: File's st_mtime at read time — used for change detection.
        """
        # Skip very large files
        if len(content.encode("utf-8", errors="replace")) > _MAX_CACHEABLE_FILE_SIZE:
            return

        is_partial = self._is_actually_partial(content, offset, limit)

        # If the file was already fully read and hasn't changed on disk,
        # don't downgrade to partial view — just refresh the timestamp.
        # This fixes the "File was only partially viewed" false positive
        # when re-reading a previously-fully-read file with offset/limit.
        if path in self._entries:
            old = self._entries[path]
            if not old.is_partial_view and (old.content == content or old.mtime == mtime):
                # Cache hit: content unchanged or mtime confirms no change.
                # Don't downgrade a full read to partial.
                self._entries.move_to_end(path)
                old.timestamp = time.monotonic()
                old.mtime = mtime
                old.was_edited = False
                return
            # Content or mtime changed — replace the entry
            self._current_size_bytes -= len(old.content.encode("utf-8", errors="replace"))
            self._entries.pop(path)

        entry = FileStateEntry(
            path=path,
            content=content,
            timestamp=time.monotonic(),
            mtime=mtime,
            offset=offset,
            limit=limit,
            is_partial_view=is_partial,
            was_edited=False,
        )

        self._entries[path] = entry
        self._current_size_bytes += len(content.encode("utf-8", errors="replace"))
        self._evict_if_needed()

    def record_edit(self, path: str, new_content: str) -> None:
        """Record that a file was edited. After edit, the file is fully known.

        Args:
            path: Absolute file path.
            new_content: File content after the edit.
        """
        if len(new_content.encode("utf-8", errors="replace")) > _MAX_CACHEABLE_FILE_SIZE:
            # Remove from cache if too large after edit
            if path in self._entries:
                old = self._entries.pop(path)
                self._current_size_bytes -= len(old.content.encode("utf-8", errors="replace"))
            return

        # Get current mtime from disk (after the edit is written)
        try:
            mtime = Path(path).stat().st_mtime
        except OSError:
            mtime = 0.0

        if path in self._entries:
            old = self._entries.pop(path)
            self._current_size_bytes -= len(old.content.encode("utf-8", errors="replace"))

        entry = FileStateEntry(
            path=path,
            content=new_content,
            timestamp=time.monotonic(),
            mtime=mtime,
            is_partial_view=False,  # After edit, we know the full content
            was_edited=True,
        )

        self._entries[path] = entry
        self._current_size_bytes += len(new_content.encode("utf-8", errors="replace"))
        self._evict_if_needed()

    def record_write(self, path: str, content: str) -> None:
        """Record that a file was written. Same semantics as record_edit."""
        self.record_edit(path, content)

    # ── Safety checks ──────────────────────────────────────────────────

    def can_edit(self, path: str) -> tuple[bool, str]:
        """Check if a file can be safely edited.

        Returns:
            Tuple of (allowed, reason).
            - (True, reason) if editing is safe.
            - (False, reason) if editing should be blocked.
        """
        if path not in self._entries:
            return True, "File not recently read"

        entry = self._entries[path]
        if entry.is_partial_view:
            return False, "File was only partially viewed. Read the full file first."

        return True, "OK"

    # ── Rebuild support ────────────────────────────────────────────────

    def get_recent(self, n: int = 5) -> list[FileStateEntry]:
        """Return the N most recently accessed files.

        Returns:
            List of FileStateEntry, most recent first.
        """
        entries = list(self._entries.values())
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[:n]

    # ── Cache management ───────────────────────────────────────────────

    def clear(self) -> None:
        """Clear all entries. Called after Full Compact."""
        self._entries.clear()
        self._current_size_bytes = 0

    def clone(self) -> FileStateCache:
        """Deep copy for sub-agent isolation.

        Sub-agents get their own snapshot. Edits in the sub-agent don't
        affect the parent's cache.
        """
        new_cache = FileStateCache(
            max_entries=self._max_entries,
            max_size_bytes=self._max_size_bytes,
        )
        for path, entry in self._entries.items():
            # Deep copy entries (strings are immutable, so this is safe)
            new_entry = FileStateEntry(
                path=entry.path,
                content=entry.content,
                timestamp=entry.timestamp,
                mtime=entry.mtime,
                offset=entry.offset,
                limit=entry.limit,
                is_partial_view=entry.is_partial_view,
                was_edited=entry.was_edited,
            )
            new_cache._entries[path] = new_entry
        new_cache._current_size_bytes = self._current_size_bytes
        return new_cache

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def size_bytes(self) -> int:
        """Current total size of cached content in bytes."""
        return self._current_size_bytes

    @property
    def entry_count(self) -> int:
        """Number of cached entries."""
        return len(self._entries)

    # ── Internal ───────────────────────────────────────────────────────

    @staticmethod
    def _is_actually_partial(
        content: str,
        offset: int | None,
        limit: int | None,
    ) -> bool:
        """Determine if the read was actually partial.

        If offset and limit were provided but cover the entire file (e.g.
        offset=0 and limit >= total_lines), it's effectively a full read.
        """
        if offset is None and limit is None:
            return False
        if offset is not None and offset > 0:
            return True
        if limit is None:
            return False
        total_lines = content.count("\n") + 1
        return limit < total_lines

    def _evict_if_needed(self) -> None:
        """Evict oldest entries if over limits."""
        while self.entry_count > self._max_entries:
            self._evict_oldest()

        while self._current_size_bytes > self._max_size_bytes and self._entries:
            self._evict_oldest()

    def _evict_oldest(self) -> None:
        """Remove the oldest entry (front of OrderedDict)."""
        if not self._entries:
            return
        _, old = self._entries.popitem(last=False)
        self._current_size_bytes -= len(old.content.encode("utf-8", errors="replace"))
