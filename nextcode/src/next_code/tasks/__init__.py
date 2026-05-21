"""Task system — unified concurrency engine for background work.

Manages the lifecycle of all async tasks (background Shell commands,
background Agent execution) with a shared state machine and registry.

Architecture (mirrors Claude Code's three-layer design):
  Layer 1: types.py — TaskType, TaskStatus, TaskStateBase + ID generation
  Layer 2: registry.py — TaskRegistry (register/get/kill)
  Layer 3: Specific implementations (bash_executor, agent_tool use registry)
"""

from .types import TaskType, TaskStatus, TaskStateBase, generate_task_id
from .registry import TaskRegistry, get_task_registry
from .disk_output import DiskTaskOutput
from .output_poller import TaskOutputPoller, start_polling, stop_polling, poll_once
from .stall_watchdog import (
    StallWatchdog,
    start_watchdog,
    stop_watchdog,
    record_task_output,
    clear_watchdogs,
)

__all__ = [
    "TaskType",
    "TaskStatus",
    "TaskStateBase",
    "generate_task_id",
    "TaskRegistry",
    "get_task_registry",
    "DiskTaskOutput",
    "TaskOutputPoller",
    "start_polling",
    "stop_polling",
    "poll_once",
    "StallWatchdog",
    "start_watchdog",
    "stop_watchdog",
    "record_task_output",
    "clear_watchdogs",
]
