"""IPC message protocol — JSON-RPC 2.0 over stdio pipes.

Defines the bidirectional message schema between Python Core and Ink Frontend.
Wire format: JSON-RPC 2.0 notification (no id) or request (with id), one per line.

    → Notification: {"jsonrpc":"2.0","method":"event.name","params":{...}}\\n
    → Request:      {"jsonrpc":"2.0","id":1,"method":"event.name","params":{...}}\\n
    ← Response:     {"jsonrpc":"2.0","id":1,"result":{...}}\\n
    ← Error:        {"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"..."}}\\n
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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

    # Agent real-time streaming
    AGENT_TEXT_DELTA = "agent.text_delta"
    AGENT_TOOL_USE = "agent.tool_use"
    AGENT_TOOL_RESULT = "agent.tool_result"
    AGENT_PROGRESS = "agent.progress"

    # Task events
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_COUNT = "task.count"
    TASK_OUTPUT = "task.output"
    TASK_STALLED = "task.stalled"

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

    # Task control
    TASK_STOP = "task.stop"
    TASK_RETAIN = "task.retain"


# Union type for all message types
MessageType = CoreToInk | InkToCore


# ── JSON-RPC 2.0 Message dataclasses ─────────────────────────────────────────

@dataclass
class JsonRpcNotification:
    """JSON-RPC 2.0 notification (no id, no response expected)."""
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> bytes:
        data: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": self.method,
            "params": self.params,
        }
        return (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    @classmethod
    def decode(cls, data: dict[str, Any]) -> JsonRpcNotification:
        return cls(
            method=data["method"],
            params=data.get("params", {}),
        )


@dataclass
class JsonRpcRequest:
    """JSON-RPC 2.0 request (has id, expects a response)."""
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int = 0

    def encode(self) -> bytes:
        data: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }
        return (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    @classmethod
    def decode(cls, data: dict[str, Any]) -> JsonRpcRequest:
        return cls(
            method=data["method"],
            params=data.get("params", {}),
            id=data["id"],
        )


@dataclass
class JsonRpcResponse:
    """JSON-RPC 2.0 success response."""
    id: int
    result: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> bytes:
        data: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self.id,
            "result": self.result,
        }
        return (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


@dataclass
class JsonRpcError:
    """JSON-RPC 2.0 error response."""
    id: int
    code: int
    message: str
    data: Any = None

    def encode(self) -> bytes:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        data: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self.id,
            "error": err,
        }
        return (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


# ── Unified decode ────────────────────────────────────────────────────────────

def decode_message(line: bytes) -> dict[str, Any]:
    """Decode a JSON-RPC 2.0 message from a raw line.

    Returns the parsed dict for dispatch. Does NOT validate structure.
    """
    return json.loads(line)


def is_request(data: dict[str, Any]) -> bool:
    return "id" in data and "method" in data


def is_notification(data: dict[str, Any]) -> bool:
    return "id" not in data and "method" in data


def is_response(data: dict[str, Any]) -> bool:
    return "id" in data and "method" not in data


# ── Legacy Message (for backward compat during migration) ─────────────────────

@dataclass
class Message:
    """Legacy IPC message — wraps JSON-RPC notification internally.

    Kept for backward compatibility with bridge.py code.
    """
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    def encode(self) -> bytes:
        return JsonRpcNotification(method=self.type, params=self.payload).encode()

    @classmethod
    def decode(cls, line: bytes) -> Message:
        data = decode_message(line)
        return cls(
            type=data.get("method", data.get("type", "")),
            payload=data.get("params", data.get("payload", {})),
            id=str(data.get("id", "")),
        )


# ── QueryEvent → IPC message mapping ─────────────────────────────────────────

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
