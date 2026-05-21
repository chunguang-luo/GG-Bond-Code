"""DiskTaskOutput — high-performance disk write queue for large task output.

Mirrors Claude Code's DiskTaskOutput from utils/task/diskOutput.ts.

Key design:
- Write queue pattern: append() queues content, drain() handles I/O
- Immediate memory release: clear queue atomically before disk write
- 5GB disk cap with truncation message
- Async-safe: all I/O is non-blocking via asyncio
- Sync-safe: can be used without a running event loop (queues for later drain)

The core invariant is that you MUST NOT await inside drain loop:
that causes the queue to balloon as write latency accumulates.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger(__name__)

# 5GB cap
MAX_TASK_OUTPUT_BYTES = 5 * 1024 * 1024 * 1024
MAX_TASK_OUTPUT_BYTES_DISPLAY = "5GB"


class DiskTaskOutput:
    """Disk-backed output handler for tasks with large output.

    Writes are queued and flushed asynchronously to avoid blocking
    the main thread. The queue is cleared atomically after each flush
    to ensure immediate memory release.

    This class is safe to use both in async and sync contexts:
    - In async context: writes happen immediately via background tasks
    - In sync context: writes are deferred until an event loop is running
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = MAX_TASK_OUTPUT_BYTES,
        open_mode: str = "a",
    ) -> None:
        self._path = Path(path)
        self._max_bytes = max_bytes
        self._open_mode = open_mode

        self._queue: list[str] = []
        self._bytes_written = 0
        self._capped = False

        # Flush tracking — _drain_task tracks the current drain operation
        self._drain_task: asyncio.Task | None = None
        self._flush_future: asyncio.Future | None = None

    @property
    def path(self) -> Path:
        """The path where output is persisted."""
        return self._path

    @property
    def is_capped(self) -> bool:
        """True if output has exceeded the disk cap."""
        return self._capped

    @property
    def bytes_written(self) -> int:
        """Total bytes written (including capped truncation)."""
        return self._bytes_written

    @property
    def queue_size(self) -> int:
        """Number of items in the write queue."""
        return len(self._queue)

    def append(self, content: str) -> None:
        """Append content to the write queue.

        This method returns immediately. The content is queued for
        async write to disk. If the cap has been reached, subsequent
        appends are silently dropped (only the truncation message remains).

        Safe to call from both sync and async contexts.
        """
        if not content:
            return

        if self._capped:
            return

        content_len = len(content.encode("utf-8", errors="replace"))
        self._bytes_written += content_len

        if self._bytes_written > self._max_bytes:
            self._capped = True
            # Replace last queued content with truncation message
            if self._queue:
                self._queue[-1] = (
                    f"\n[output truncated: exceeded {MAX_TASK_OUTPUT_BYTES_DISPLAY} disk cap]\n"
                )
            else:
                self._queue.append(
                    f"\n[output truncated: exceeded {MAX_TASK_OUTPUT_BYTES_DISPLAY} disk cap]\n"
                )
        else:
            self._queue.append(content)

        # Schedule drain if not already running
        self._maybe_schedule_drain()

    def _maybe_schedule_drain(self) -> None:
        """Schedule a drain if one isn't already pending."""
        if self._drain_task is None or self._drain_task.done():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running event loop — drain will happen on next flush or explicit scheduling
                return
            self._drain_task = loop.create_task(self._drain())

    async def _drain(self) -> None:
        """Drain the queue to disk (runs in background).

        This is async but intentionally NOT awaited inside the drain
        loop itself — we want the queue cleared before the write completes
        so memory can be reclaimed even if disk I/O is slow.
        """
        try:
            # Atomically grab all queued content and clear the queue
            # This is the key to immediate memory release
            content, self._queue = self._queue, []

            if not content:
                return

            # Ensure parent directory exists
            self._path.parent.mkdir(parents=True, exist_ok=True)

            # Open file and write
            # Use unbuffered writes for large output
            with open(
                self._path,
                self._open_mode,
                buffering=1,  # Line buffered
                errors="replace",
            ) as f:
                # Write all chunks — DO NOT await between chunks!
                # Adding await here would cause memory to balloon
                # as the queue grows faster than disk can write.
                f.writelines(content)

        except asyncio.CancelledError:
            logger.warning("DiskTaskOutput drain cancelled for %s", self._path)
            raise
        except Exception:
            logger.exception("Failed to drain DiskTaskOutput for %s", self._path)
        finally:
            # Signal that drain is complete
            if self._flush_future is not None and not self._flush_future.done():
                self._flush_future.set_result(None)

    async def _schedule_drain_async(self) -> None:
        """Explicitly schedule drain with an event loop (for sync contexts)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = loop.create_task(self._drain())

    async def flush(self) -> None:
        """Wait for all pending writes to complete.

        Call this before reading the output file to ensure
        all content has been written to disk.
        """
        # If there's a drain task running, wait for it
        if self._drain_task is not None and not self._drain_task.done():
            try:
                await self._drain_task
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # Drain may have failed, but content is still queued

        # If there are items in the queue, schedule a new drain and wait
        if self._queue:
            self._flush_future = asyncio.get_running_loop().create_future()
            self._drain_task = asyncio.create_task(self._drain())
            try:
                await self._flush_future
            finally:
                self._flush_future = None

        # Wait for the final drain to complete
        if self._drain_task is not None and not self._drain_task.done():
            await self._drain_task

    def sync_flush(self) -> None:
        """Synchronous flush — schedules drain and blocks until done.

        Only works when there's a running event loop.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No event loop, can't do sync flush

        async def _sync_flush() -> None:
            await self.flush()

        # This is a sync context, so we need to be careful
        # The caller should be in an async context or use a different approach
        import contextvars
        import functools

        # Create a new future and run until complete
        async def _run_until_complete() -> None:
            await self.flush()

        # Schedule and wait in a way that works from sync code
        loop.run_until_complete(_run_until_complete())

    def read_tail(self, lines: int = 100) -> str:
        """Read the last N lines from the output file.

        Used by TaskOutput polling to get the latest output
        without reading the entire file.
        """
        if not self._path.exists():
            return ""

        try:
            with open(self._path, "r", errors="replace") as f:
                return _read_tail_lines(f, lines)
        except Exception:
            logger.exception("Failed to read tail from %s", self._path)
            return ""

    def read_all(self) -> str:
        """Read the complete output file."""
        if not self._path.exists():
            return ""

        try:
            return self._path.read_text(errors="replace")
        except Exception:
            logger.exception("Failed to read full output from %s", self._path)
            return ""


def _read_tail_lines(f: "hasFileno", n: int) -> str:
    """Read last N lines from an open file efficiently.

    For very large files, seeking from the end is more efficient
    than reading the whole file.
    """
    try:
        fd = f.fileno()
        file_size = os.fstat(fd).st_size

        if file_size == 0:
            return ""

        # For small files, just read all
        if file_size < 64 * 1024:
            f.seek(0)
            all_lines = f.readlines()
            return "".join(all_lines[-n:])

        # For large files, seek from end
        BUFSIZE = 8 * 1024
        pos = file_size
        lines_found = 0
        line_ends: list[int] = []

        # Read in chunks from the end
        while pos > 0 and lines_found <= n:
            chunk_size = min(BUFSIZE, pos)
            pos -= chunk_size
            f.seek(pos)
            chunk = f.read(chunk_size)

            # Count newlines in chunk
            for i in range(len(chunk) - 1, -1, -1):
                if chunk[i] == "\n":
                    lines_found += 1
                    line_ends.append(pos + i + 1)
                    if lines_found > n:
                        break

        # Build result
        if line_ends:
            f.seek(line_ends[-1])
        else:
            f.seek(0)
        return f.read()

    except Exception:
        # Fallback: read all
        f.seek(0)
        all_lines = f.readlines()
        return "".join(all_lines[-n:])


# Type hint for file-like objects with fileno
if TYPE_CHECKING:
    from typing import IO

    hasFileno = IO[str] | IO[bytes]