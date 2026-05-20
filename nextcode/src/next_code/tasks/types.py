"""Task type definitions — Layer 1 of the task system.

Defines the shared vocabulary (TaskType, TaskStatus, TaskStateBase)
and ID generation logic that all task implementations use.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any


class TaskType(str, enum.Enum):
    """Task type identifiers — each type has a unique ID prefix."""

    LOCAL_BASH = "local_bash"
    LOCAL_AGENT = "local_agent"


class TaskStatus(str, enum.Enum):
    """Task lifecycle states — shared by all task types.

    State machine:
        pending → running → completed
                          → failed
                          → killed
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


# ID prefix per task type — mirrors Claude Code's TASK_ID_PREFIXES
_TASK_ID_PREFIXES: dict[str, str] = {
    TaskType.LOCAL_BASH.value: "b",      # b3k9m2x7p
    TaskType.LOCAL_AGENT.value: "a",     # a7f2h8k3m
}

_TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED}


def generate_task_id(task_type: TaskType) -> str:
    """Generate a unique task ID with type-specific prefix.

    Format: prefix + 8 hex characters (e.g. 'b3k9m2x7p')
    """
    prefix = _TASK_ID_PREFIXES.get(task_type.value, "x")
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def is_terminal_status(status: TaskStatus) -> bool:
    """Check if a task status is terminal (no further transitions)."""
    return status in _TERMINAL_STATUSES


@dataclass
class TaskStateBase:
    """Base state for all task types — stored in TaskRegistry.

    Subclass this for task-type-specific state (e.g. LocalShellTaskState
    adds command/result, LocalAgentTaskState adds agent_id/prompt).
    """

    id: str
    type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    command: str = ""  # Bash command or Agent prompt
    description: str = ""  # Short human-readable task description (from model)
    started_at: float = 0.0
    result: str | None = None
    agent_id: str | None = None  # Which agent created this task
    notified: bool = False  # Prevents duplicate completion notifications

    # Process/subprocess handles — set by the specific task implementation
    _process: Any = None  # asyncio.subprocess.Process for bash tasks
    _asyncio_task: Any = None  # asyncio.Task for agent tasks
    _output_path: str | None = None  # Path to output file for bash tasks
    _completed_at: float = 0.0  # monotonic timestamp when task reached terminal state

    def is_terminal(self) -> bool:
        return is_terminal_status(self.status)
