"""TaskOutputPoller — UI-driven polling for background task output.

Mirrors Claude Code's TaskOutput polling from utils/task/TaskOutput.ts.

Key design:
- Static registry: all active pollers in class-level Map
- On-demand resource allocation: interval only runs when there are pollers
- File tail tracking: efficient last-N-lines reading via DiskTaskOutput
- Async-safe: all operations are non-blocking

This implements "UI-driven on-demand polling" — the frontend decides which
tasks to poll (via startPolling/stopPolling), and the poller automatically
stops when no tasks are being watched.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# Polling interval in seconds
DEFAULT_POLL_INTERVAL_S = 0.5


class TaskOutputPoller:
    """Poller for a specific task's output.

    Each poller is tied to one task_id and reads from its output file.
    When the task completes, the poller stops automatically.
    """

    # Class-level registry of all active pollers
    _registry: dict[str, TaskOutputPoller] = {}
    _poll_interval: float | None = None

    def __init__(
        self,
        task_id: str,
        output_path: str | Path,
        on_output: Callable[[str], Awaitable[None]] | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self.task_id = task_id
        self.output_path = Path(output_path)
        self.on_output = on_output
        self.poll_interval = poll_interval

        # Track the last position we read (for incremental reads)
        self._last_pos = 0
        self._is_running = False
        self._poll_task: asyncio.Task | None = None

        # State callbacks (for task state queries)
        self._get_task_state: Callable[[str], Any] | None = None

    def set_task_state_callback(self, callback: Callable[[str], Any]) -> None:
        """Set callback to get task state (for checking completion)."""
        self._get_task_state = callback

    def _is_task_terminal(self) -> bool:
        """Check if the task has reached a terminal state."""
        if self._get_task_state is None:
            return False
        task = self._get_task_state(self.task_id)
        if task is None:
            return True  # Task evicted = terminal
        return task.is_terminal()

    async def _poll_loop(self) -> None:
        """Main polling loop — read tail and notify."""
        from ..tasks.disk_output import DiskTaskOutput

        disk_output = DiskTaskOutput(self.output_path)
        last_output = ""

        while self._is_running:
            await asyncio.sleep(self.poll_interval)

            # Check if task is done
            if self._is_task_terminal():
                # Final read — get all remaining output
                if self.on_output:
                    final_output = disk_output.read_all()
                    if final_output != last_output:
                        await self.on_output(final_output)
                break

            # Read tail from disk
            tail = disk_output.read_tail(lines=50)

            # Only notify if content changed
            if tail != last_output:
                last_output = tail
                if self.on_output:
                    try:
                        await self.on_output(tail)
                    except Exception:
                        logger.exception("Poller callback error for task %s", self.task_id)

        # Remove from registry when done
        self.stop()

    def start(self) -> None:
        """Start polling this task's output."""
        if self._is_running:
            return

        self._is_running = True
        loop = asyncio.get_running_loop()
        self._poll_task = loop.create_task(self._poll_loop(), name=f"TaskOutputPoller.{self.task_id}")

        # Register in class registry
        TaskOutputPoller._registry[self.task_id] = self

        # Start global interval if this is the first poller
        if len(TaskOutputPoller._registry) == 1:
            TaskOutputPoller._start_global_interval()

    def stop(self) -> None:
        """Stop polling this task."""
        self._is_running = False

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()

        # Remove from registry
        TaskOutputPoller._registry.pop(self.task_id, None)

        # Stop global interval if no pollers remain
        if not TaskOutputPoller._registry:
            TaskOutputPoller._stop_global_interval()

    @classmethod
    def get_poller(cls, task_id: str) -> TaskOutputPoller | None:
        """Get an existing poller for a task."""
        return cls._registry.get(task_id)

    @classmethod
    def list_active(cls) -> list[str]:
        """List all task IDs being polled."""
        return list(cls._registry.keys())

    # ── Global interval management ────────────────────────────────────────────

    # Store the global interval handle
    _global_interval: asyncio.TimerHandle | None = None
    _global_loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def _start_global_interval(cls) -> None:
        """Start the global polling interval if any pollers exist.

        The global interval is a safety net — it ensures the event loop
        isn't blocked by our background tasks. When .unref() is called,
        the loop can exit even if this interval is pending.
        """
        if cls._registry and cls._global_interval is None:
            cls._global_loop = asyncio.get_running_loop()
            # Schedule a no-op periodically to keep the loop alive
            # This ensures async operations can complete
            def _tick() -> None:
                if not cls._registry:
                    cls._stop_global_interval()
                    return
                # Reschedule
                if cls._global_loop:
                    cls._global_interval = cls._global_loop.call_later(
                        60.0, _tick
                    )

            cls._global_interval = cls._global_loop.call_later(60.0, _tick)

    @classmethod
    def _stop_global_interval(cls) -> None:
        """Stop the global interval when no pollers are active."""
        cls._global_interval = None
        cls._global_loop = None


# ── Convenience functions ────────────────────────────────────────────────────


async def start_polling(
    task_id: str,
    output_path: str | Path,
    on_output: Callable[[str], Awaitable[None]] | None = None,
    get_task_state: Callable[[str], Any] | None = None,
) -> TaskOutputPoller:
    """Start polling a task's output.

    Args:
        task_id: The task ID to poll
        output_path: Path to the task's output file
        on_output: Callback called with new output lines
        get_task_state: Callback to check task completion

    Returns:
        The created poller
    """
    # Check if already polling
    existing = TaskOutputPoller.get_poller(task_id)
    if existing:
        return existing

    poller = TaskOutputPoller(task_id, output_path, on_output)
    if get_task_state:
        poller.set_task_state_callback(get_task_state)

    poller.start()
    return poller


async def stop_polling(task_id: str) -> bool:
    """Stop polling a task.

    Returns True if a poller was stopped, False if not found.
    """
    poller = TaskOutputPoller.get_poller(task_id)
    if poller:
        poller.stop()
        return True
    return False


async def poll_once(
    task_id: str,
    output_path: str | Path,
    tail_lines: int = 50,
) -> str:
    """Poll once and return the tail of the task's output.

    This is a convenience function for one-shot polling without
    starting a persistent poller.
    """
    from .disk_output import DiskTaskOutput

    disk_output = DiskTaskOutput(output_path)
    return disk_output.read_tail(lines=tail_lines)