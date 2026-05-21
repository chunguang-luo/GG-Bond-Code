"""StallWatchdog — detect when background tasks are stuck on interactive input.

Mirrors Claude Code's startStallWatchdog() from LocalShellTask.

Key design:
- 45-second threshold: tasks with no output for 45s are checked
- Pattern matching: last line of output matched against known interactive prompts
- Non-blocking notification: sends <task-notification> to model without killing
- Per-task: each background task gets its own watchdog

Common patterns detected:
- pip/yum confirmation: "Do you want to proceed? [Y/n]"
- apt confirmation: "Do you want to continue? [Y/n]"
- sudo password: "Password:"
- Ctrl+C prompt: "_interrupt"
- Press any key: "Press any key"
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

# Time threshold before checking for stall (45 seconds)
STALL_THRESHOLD_MS = 45_000
STALL_CHECK_INTERVAL_MS = 5_000  # Check every 5 seconds after threshold


# Known interactive prompt patterns — these indicate the process is waiting
# for user input and will not make progress on its own.
INTERACTIVE_PATTERNS: list[tuple[str, str]] = [
    # Package managers
    (r"\(Y/n\)|\[[Yy]/[Nn]\]|\[[Yy]es?/[Nn]o?\]", "Package manager confirmation"),
    (r"Do you want to (continue|proceed)", "Package manager confirmation"),
    (r"Press Y to continue", "Package manager confirmation"),
    (r"Install these packages without verification", "Package manager warning"),
    (r"\? \[Y/n\] \? \?", "Package installer prompt"),

    # Password prompts
    (r"Password:", "Password prompt"),
    (r"\[sudo\] password", "Sudo password prompt"),

    # Interactive programs
    (r"Press any key to", "Press any key prompt"),
    (r"Press Enter to", "Press Enter prompt"),
    (r"Hit enter to", "Press Enter prompt"),

    # Overwrite confirmations
    (r"Overwrite.*\? \[Yy\]", "Overwrite confirmation"),

    # SSH/certificate confirmations
    (r"Are you sure you want to continue", "SSH fingerprint confirmation"),
    (r"ECDSA host key", "SSH host key confirmation"),

    # git confirmations
    (r" Abdominal ", "git abort confirmation"),  # rare but appears in some git hooks
]

import re

# Compile patterns for efficiency
_COMPILED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(pattern, re.IGNORECASE), description)
    for pattern, description in INTERACTIVE_PATTERNS
]


class StallWatchdog:
    """Watchdog for detecting stalled background tasks.

    After STALL_THRESHOLD_MS of no output, starts polling the task's
    output file to check for interactive prompts. If detected, notifies
    the model so it can decide whether to kill or send input.
    """

    def __init__(
        self,
        task_id: str,
        output_path: str,
        on_stall_detected: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self.task_id = task_id
        self.output_path = output_path
        self.on_stall_detected = on_stall_detected

        self._last_output_time: float = time.monotonic()
        self._watchdog_task: asyncio.Task | None = None
        self._is_running = False

    def record_output(self) -> None:
        """Record that output was received — resets the stall timer."""
        self._last_output_time = time.monotonic()

    def start(self) -> None:
        """Start the watchdog timer."""
        if self._is_running:
            return

        self._is_running = True
        self._watchdog_task = asyncio.create_task(self._run_watchdog())

    def stop(self) -> None:
        """Stop the watchdog."""
        self._is_running = False
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = None

    async def _run_watchdog(self) -> None:
        """Main watchdog loop — checks for stall after threshold."""
        from ..tasks.disk_output import DiskTaskOutput

        disk_output = DiskTaskOutput(self.output_path)

        while self._is_running:
            await asyncio.sleep(1.0)  # Check every second

            # Check if we've exceeded the stall threshold
            elapsed_ms = (time.monotonic() - self._last_output_time) * 1000
            if elapsed_ms < STALL_THRESHOLD_MS:
                continue

            # We're past the threshold — check for interactive prompts
            last_line = self._check_last_line(disk_output)

            if last_line:
                # Found something that might indicate stall
                description = self._detect_prompt_type(last_line)
                if description:
                    logger.info(
                        "Stall detected for task %s: %s (last line: %r)",
                        self.task_id, description, last_line,
                    )

                    # Notify via callback
                    if self.on_stall_detected:
                        try:
                            await self.on_stall_detected(self.task_id, description)
                        except Exception:
                            logger.exception("Stall notification failed for %s", self.task_id)

            # Continue polling every few seconds until task completes
            await asyncio.sleep(STALL_CHECK_INTERVAL_MS / 1000)

    def _check_last_line(self, disk_output: "DiskTaskOutput") -> str:
        """Read the last non-empty line from the output file."""
        content = disk_output.read_tail(lines=10)
        if not content:
            return ""

        lines = content.split("\n")
        # Find last non-empty line
        for line in reversed(lines):
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    def _detect_prompt_type(self, last_line: str) -> str | None:
        """Check if the last line matches a known interactive prompt pattern."""
        for pattern, description in _COMPILED_PATTERNS:
            if pattern.search(last_line):
                return description
        return None


# ── Global watchdog registry ──────────────────────────────────────────────────


# task_id -> StallWatchdog instance
_watchdogs: dict[str, StallWatchdog] = {}


def start_watchdog(
    task_id: str,
    output_path: str,
    on_stall_detected: Callable[[str, str], Awaitable[None]] | None = None,
) -> StallWatchdog:
    """Start a watchdog for a background task."""
    if task_id in _watchdogs:
        _watchdogs[task_id].stop()

    watchdog = StallWatchdog(task_id, output_path, on_stall_detected)
    watchdog.start()
    _watchdogs[task_id] = watchdog
    return watchdog


def stop_watchdog(task_id: str) -> None:
    """Stop the watchdog for a task."""
    watchdog = _watchdogs.pop(task_id, None)
    if watchdog:
        watchdog.stop()


def record_task_output(task_id: str) -> None:
    """Record that a task produced output (resets its watchdog timer)."""
    watchdog = _watchdogs.get(task_id)
    if watchdog:
        watchdog.record_output()


def get_watchdog(task_id: str) -> StallWatchdog | None:
    """Get the watchdog for a task, if any."""
    return _watchdogs.get(task_id)


def clear_watchdogs() -> None:
    """Clear all watchdogs (called on session reset)."""
    for watchdog in _watchdogs.values():
        watchdog.stop()
    _watchdogs.clear()