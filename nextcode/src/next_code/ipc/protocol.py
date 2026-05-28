"""IPC message protocol — type definitions and validation.

Defines the bidirectional message schema between Python Core and Ink Frontend.
Wire format: JSON lines terminated by \\n over Unix domain socket.

    {"type": "event_name", "id": "optional-correlation-id", "payload": {...}}\\n
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# ── Message types ────────────────────────────────────────────────────────────

class CoreToInk(str, Enum):
    """Python → Ink message types."""

    # Session lifecycle
    SESSION_READY = "session.ready"
    SESSION_SHUTDOWN = "session.shutdown"

    # Query events (1:1 mapping from QueryEvent)
    QUERY_TEXT_DELTA = "query.text_delta"
    QUERY_THINKING_DELTA = "query.thinking_delta"
    QUERY_TOOL_START = "query.tool_start"
    QUERY_TOOL_USE = "query.tool_use"
    QUERY_TOOL_RESULT = "query.tool_result"
    QUERY_ERROR = "query.error"
    QUERY_WARNING = "query.warning"
    QUERY_COMPLETE = "query.complete"
    QUERY_INFO = "query.info"
    QUERY_CLEARED = "query.cleared"
    QUERY_QUEUED = "query.queued"
    QUERY_DEQUEUE = "query.dequeue"

    # State synchronization
    STATE_UPDATE = "state.update"
    STATE_SNAPSHOT = "state.snapshot"

    # Permission
    PERMISSION_REQUEST = "permission.request"
    PERMISSION_MODE_UPDATE = "permission.mode_update"

    # Context info
    CONTEXT_INFO = "context.info"

    # Welcome screen
    WELCOME = "welcome"

    # Compact notifications
    COMPACT_STARTED = "compact.started"
    COMPACT_COMPLETE = "compact.complete"

    # Command list sync
    COMMANDS_UPDATE = "commands.update"

    # Agent lifecycle
    AGENT_START = "agent.start"
    AGENT_RESULT = "agent.result"

    # Agent real-time streaming — sub-agent text/tool events forwarded
    # with "agent." prefix so the frontend can render them distinctly
    # from the parent conversation's events.
    AGENT_TEXT_DELTA = "agent.text_delta"
    AGENT_TOOL_USE = "agent.tool_use"
    AGENT_TOOL_RESULT = "agent.tool_result"
    AGENT_PROGRESS = "agent.progress"

    # Task events — background task lifecycle
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_COUNT = "task.count"  # Periodic background task count update
    TASK_OUTPUT = "task.output"  # Real-time output streaming (tail) for running tasks
    TASK_STALLED = "task.stalled"  # Task may be waiting for interactive input (e.g., apt confirmation)

    # Heartbeat
    PING = "ping"


class InkToCore(str, Enum):
    """Ink → Python message types."""

    # Session lifecycle
    READY = "ready"
    PONG = "pong"

    # User input
    USER_MESSAGE = "user.message"
    USER_INTERRUPT = "user.interrupt"
    USER_COMMAND = "user.command"

    # Permission response
    PERMISSION_RESPONSE = "permission.response"
    PERMISSION_MODE_CYCLE = "permission.mode_cycle"

    # UI state
    UI_TOGGLE_THINKING = "ui.toggle_thinking"
    UI_RESIZE = "ui.resize"

    # Theme
    THEME_CHANGE = "theme.change"

    # Shutdown
    SHUTDOWN_ACK = "shutdown.ack"

    # Task control — frontend tells backend to manage background tasks
    TASK_STOP = "task.stop"  # Stop a running task
    TASK_RETAIN = "task.retain"  # Mark a task to stay visible


# Union type for all message types
MessageType = CoreToInk | InkToCore


# ── Message dataclass ────────────────────────────────────────────────────────

@dataclass
class Message:
    """IPC message with type, optional correlation ID, and payload."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    def encode(self) -> bytes:
        """Serialize to JSON line (with \\n terminator)."""
        data: dict[str, Any] = {"type": self.type}
        if self.id:
            data["id"] = self.id
        data["payload"] = self.payload
        return (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    @classmethod
    def decode(cls, line: bytes) -> Message:
        """Deserialize from a JSON line (without \\n terminator)."""
        data = json.loads(line)
        return cls(
            type=data["type"],
            payload=data.get("payload", {}),
            id=data.get("id", ""),
        )

    def __repr__(self) -> str:
        payload_repr = json.dumps(self.payload, ensure_ascii=False)
        if len(payload_repr) > 120:
            payload_repr = payload_repr[:117] + "..."
        return f"Message(type={self.type!r}, id={self.id!r}, payload={payload_repr})"


# ── QueryEvent → IPC message mapping ─────────────────────────────────────────

# Maps QueryEvent.type values to CoreToInk message types
QUERY_EVENT_MAP: dict[str, CoreToInk] = {
    "text": CoreToInk.QUERY_TEXT_DELTA,
    "thinking": CoreToInk.QUERY_THINKING_DELTA,
    "tool_start": CoreToInk.QUERY_TOOL_START,
    "tool_use": CoreToInk.QUERY_TOOL_USE,
    "tool_result": CoreToInk.QUERY_TOOL_RESULT,
    "error": CoreToInk.QUERY_ERROR,
    "warning": CoreToInk.QUERY_WARNING,
    "agent_start": CoreToInk.AGENT_START,
    "agent_result": CoreToInk.AGENT_RESULT,
}


# ── Permission decision values ────────────────────────────────────────────────

class PermissionDecisionValue(str, Enum):
    """Permission decision values used in IPC messages."""
    ALLOW = "allow"
    DENY = "deny"
    ALWAYS_ALLOW = "always_allow"