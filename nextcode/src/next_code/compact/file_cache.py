"""FileStateCache — track file read state for edit safety and post-compact rebuild.

Core semantics:
- is_partial_view: files read with offset/limit are flagged as partial;
  editing a partially-viewed file is blocked because unseen content may be damaged.
- get_recent(): returns most recently accessed files, used to re-inject
  file contents after Full Compact so the model doesn't "lose memory".
- LRU eviction with dual limits (max entries + max size).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field


# Skip caching files larger than this to avoid excessive memory use
_MAX_CACHEABLE_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


@dataclass
class FileStateEntry:
    """A single file's read/edit state in the cache."""

    path: str
    content: str  # Full file content at read/edit time (for diff and rebuild)
    timestamp: float  # time.monotonic()
    offset: int | None = None  # Read offset parameter
    limit: int | None = None  # Read limit parameter
    is_partial_view: bool = False  # True if only part of the file was read
    was_edited: bool = False  # True if the file was edited/written since last read


class FileStateCache:
    """LRU cache tracking file read/edit state.

    Two roles:
    1. Safety guard: block edits to partially-viewed files (can_edit).
    2. Rebuild index: provide recently-accessed files for post-compact
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

    # ── Recording ──────────────────────────────────────────────────────

    def record_read(
        self,
        path: str,
        content: str,
        offset: int | None = None,
        limit: int | None = None,
    ) -> None:
        """Record that a file was read.

        Args:
            path: Absolute file path.
            content: Full file content (even if offset/limit was used for display).
            offset: Read offset parameter (None = from start).
            limit: Read limit parameter (None = to end).
        """
        # Skip very large files
        if len(content.encode("utf-8", errors="replace")) > _MAX_CACHEABLE_FILE_SIZE:
            return

        is_partial = offset is not None or limit is not None

        # Remove old entry if exists (updates position + size)
        if path in self._entries:
            old = self._entries.pop(path)
            self._current_size_bytes -= len(old.content.encode("utf-8", errors="replace"))

        entry = FileStateEntry(
            path=path,
            content=content,
            timestamp=time.monotonic(),
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

        if path in self._entries:
            old = self._entries.pop(path)
            self._current_size_bytes -= len(old.content.encode("utf-8", errors="replace"))

        entry = FileStateEntry(
            path=path,
            content=new_content,
            timestamp=time.monotonic(),
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
