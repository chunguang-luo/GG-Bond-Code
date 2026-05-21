"""TaskRegistry — Layer 2 of the task system.

Centralized registry that tracks all running and recently completed tasks.
Supports registration, lookup, kill dispatch, and cleanup of terminal tasks.

Mirrors Claude Code's getAllTasks()/getTaskByType() pattern, but simplified
for the Python asyncio runtime — we use a single dict + kill handlers
instead of a separate tasks.ts file.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any, Callable, Awaitable

from .types import TaskStateBase, TaskType, TaskStatus, is_terminal_status

logger = logging.getLogger(__name__)

# Kill handler signature: receives the task state, returns True if killed
KillHandler = Callable[[TaskStateBase], Awaitable[bool]]

# How long to keep terminal tasks before eviction (seconds)
EVICTION_DELAY = 30.0


class TaskRegistry:
    """Centralized task registry with kill dispatch.

    Thread-safe by design — asyncio is single-threaded, so no locks needed.
    All mutations happen on the event loop.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskStateBase] = {}
        self._kill_handlers: dict[TaskType, KillHandler] = {}

    # ── Registration ──────────────────────────────────────────────

    # Fields to preserve when re-registering an existing task (for resume scenarios)
    _PRESERVE_FIELDS: tuple[str, ...] = (
        "retain",       # User marked to stay visible
        "started_at",   # Original start time (sorting stability)
        "result",       # Previous result (if any)
        "notified",     # Already notified (don't notify again)
        "_output_path", # Output file path
        "_disk_output", # DiskTaskOutput instance
    )

    def register(self, task: TaskStateBase) -> None:
        """Register a new task. Merges UI state for resume scenarios.

        When a task is re-registered (e.g., after session restart), preserves
        user-facing state like retain flag, started_at, result, and notified
        status to maintain continuity.
        """
        import time as _time

        existing = self._tasks.get(task.id)
        if existing:
            # Merge UI state from existing task into the new registration
            if existing.status == TaskStatus.RUNNING and task.status == TaskStatus.RUNNING:
                # Task is still running — merge state but keep it running
                logger.debug(
                    "Re-registering running task %s, preserving UI state",
                    task.id,
                )
            else:
                logger.debug(
                    "Task %s already exists (status=%s), merging UI state",
                    task.id, existing.status.value,
                )

            for field_name in self._PRESERVE_FIELDS:
                if not hasattr(existing, field_name):
                    continue
                old_value = getattr(existing, field_name)
                # For retain: preserve old value if new task has default (False)
                # This allows user-marked retain to survive re-registration
                if field_name == "retain" and not task.retain and old_value:
                    setattr(task, field_name, old_value)
                elif field_name != "retain":
                    # For other fields, always preserve from old task
                    setattr(task, field_name, old_value)

            # If existing task was terminal and notified, carry that forward
            if existing.is_terminal() and existing.notified:
                task.notified = True
                task._completed_at = existing._completed_at

            # Set evict_after for retained tasks (30 seconds from now)
            if task.retain:
                task.evict_after = _time.monotonic() + self.EVICTION_GRACE_PERIOD
                logger.debug("Task %s marked for retention until %f", task.id, task.evict_after)

        self._tasks[task.id] = task
        logger.info("Task registered: %s (type=%s, retain=%s)", task.id, task.type.value, task.retain)

    def mark_retain(self, task_id: str, retain: bool = True) -> bool:
        """Mark a task to be retained in the UI (don't auto-evict).

        Args:
            task_id: The task ID to mark
            retain: True to retain, False to unmark

        Returns True if the task was found and marked.
        """
        import time as _time

        task = self._tasks.get(task_id)
        if task is None:
            return False

        task.retain = retain
        if retain:
            task.evict_after = _time.monotonic() + self.EVICTION_GRACE_PERIOD
            logger.debug("Task %s marked for retention", task_id)
        else:
            task.evict_after = 0.0
            logger.debug("Task %s unmark retention", task_id)

        return True

    def mark_disk_loaded(self, task_id: str) -> bool:
        """Mark a task's output as loaded from disk.

        Used to prevent redundant disk reads when the UI has already
        loaded the task's output.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.disk_loaded = True
        return True

    def register_kill_handler(self, task_type: TaskType, handler: KillHandler) -> None:
        """Register a kill handler for a task type.

        Called by task implementations to provide their kill logic.
        Mirrors Claude Code's Task.kill interface, but uses a registry
        pattern instead of per-instance methods.
        """
        self._kill_handlers[task_type] = handler

    # ── Query ────────────────────────────────────────────────────

    def get(self, task_id: str) -> TaskStateBase | None:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def list_all(self) -> list[TaskStateBase]:
        """List all registered tasks."""
        return list(self._tasks.values())

    def list_by_type(self, task_type: TaskType) -> list[TaskStateBase]:
        """List all tasks of a given type."""
        return [t for t in self._tasks.values() if t.type == task_type]

    def list_running(self) -> list[TaskStateBase]:
        """List all currently running tasks."""
        return [t for t in self._tasks.values() if t.status == TaskStatus.RUNNING]

    def running_count(self) -> tuple[int, int]:
        """Return (bash_count, agent_count) of running tasks."""
        bash = 0
        agent = 0
        for t in self._tasks.values():
            if t.status != TaskStatus.RUNNING:
                continue
            if t.type == TaskType.LOCAL_BASH:
                bash += 1
            elif t.type == TaskType.LOCAL_AGENT:
                agent += 1
        return bash, agent

    def list_for_agent(self, agent_id: str) -> list[TaskStateBase]:
        """List all tasks created by a specific agent (for cleanup on agent exit)."""
        return [
            t for t in self._tasks.values()
            if t.agent_id == agent_id and t.status == TaskStatus.RUNNING
        ]

    # ── Update ──────────────────────────────────────────────────

    def update(self, task_id: str, **kwargs: Any) -> bool:
        """Update task state fields. Returns False if task not found or terminal."""
        import time as _time
        task = self._tasks.get(task_id)
        if task is None:
            return False
        if task.is_terminal() and "notified" not in kwargs:
            # Only allow updating 'notified' on terminal tasks
            return False
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        # Auto-set _completed_at when task reaches terminal state
        if task.is_terminal() and task._completed_at == 0.0:
            task._completed_at = _time.monotonic()
        return True

    def mark_notified(self, task_id: str) -> bool:
        """Atomically mark a task as notified. Returns True if this was the first notification.

        Prevents duplicate notifications from kill + natural completion racing.
        Mirrors Claude Code's notified atomic check pattern.
        """
        task = self._tasks.get(task_id)
        if task is None or task.notified:
            return False
        task.notified = True
        return True

    # ── Kill ─────────────────────────────────────────────────────

    async def kill(self, task_id: str) -> bool:
        """Kill a task by dispatching to its registered kill handler.

        Returns True if the task was killed, False if not found or already terminal.
        """
        task = self._tasks.get(task_id)
        if task is None:
            logger.warning("Kill requested for unknown task %s", task_id)
            return False
        if task.is_terminal():
            return False

        handler = self._kill_handlers.get(task.type)
        if handler is None:
            logger.error("No kill handler for task type %s", task.type.value)
            task.status = TaskStatus.KILLED
            return True

        try:
            killed = await handler(task)
            if killed:
                task.status = TaskStatus.KILLED
            return killed
        except Exception:
            logger.exception("Kill handler failed for task %s", task_id)
            task.status = TaskStatus.KILLED
            return True

    async def kill_for_agent(self, agent_id: str) -> int:
        """Kill all running tasks created by a specific agent.

        Called when an agent exits to prevent zombie processes.
        Mirrors Claude Code's killShellTasksForAgent().
        """
        tasks_to_kill = self.list_for_agent(agent_id)
        count = 0
        for task in tasks_to_kill:
            killed = await self.kill(task.id)
            if killed:
                count += 1
        if count:
            logger.info("Killed %d tasks for agent %s", count, agent_id)
        return count

    # ── Eviction ─────────────────────────────────────────────────

    # How long to keep terminal tasks after notification before eviction (seconds)
    EVICTION_GRACE_PERIOD = 300.0  # 5 minutes — enough time for TaskOutput queries

    def evict_terminal(self) -> None:
        """Remove terminal tasks that have been notified and are past the grace period.

        Mirrors Claude Code's evictTerminalTask(). Called periodically
        to prevent memory buildup from completed tasks.

        Tasks are kept for EVICTION_GRACE_PERIOD after being notified,
        so that TaskOutput can still retrieve their results.
        Tasks with retain=True are kept until their evict_after timestamp.
        """
        import time
        now = time.monotonic()
        to_evict = []

        for tid, task in self._tasks.items():
            # Must be terminal and notified
            if not (task.is_terminal() and task.notified):
                continue

            # Check grace period from completion
            if task._completed_at > 0 and (now - task._completed_at) <= self.EVICTION_GRACE_PERIOD:
                continue

            # Check retain deadline
            if task.retain and task.evict_after > 0 and now < task.evict_after:
                continue  # Still within retention period

            to_evict.append(tid)

        for tid in to_evict:
            task = self._tasks[tid]
            logger.debug(
                "Evicting task %s (retain=%s, completed_at=%f, evict_after=%f, now=%f)",
                tid, task.retain, task._completed_at, task.evict_after, now,
            )
            del self._tasks[tid]

    def clear(self) -> None:
        """Clear all tasks (used on /clear or session reset)."""
        self._tasks.clear()


# ── Module-level singleton ──────────────────────────────────────────

_registry: TaskRegistry | None = None


def get_task_registry() -> TaskRegistry:
    """Get the global TaskRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = TaskRegistry()
    return _registry


def reset_task_registry() -> TaskRegistry:
    """Reset the global TaskRegistry (for testing or session reset)."""
    global _registry
    _registry = TaskRegistry()
    return _registry
