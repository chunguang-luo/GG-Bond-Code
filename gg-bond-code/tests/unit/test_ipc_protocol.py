"""Tests for IPC protocol — message encoding/decoding and type definitions."""

import json
import pytest

from gg_bond_code.ipc.protocol import (
    Message,
    CoreToInk,
    InkToCore,
    QUERY_EVENT_MAP,
    PermissionDecisionValue,
)


class TestMessage:
    """Test Message dataclass encode/decode."""

    def test_encode_basic(self):
        msg = Message(type="session.ready", payload={"model": "deepseek-chat", "cwd": "/tmp"})
        data = msg.encode()
        assert data.endswith(b"\n")
        parsed = json.loads(data.strip())
        assert parsed["type"] == "session.ready"
        assert parsed["payload"]["model"] == "deepseek-chat"
        assert "id" not in parsed  # No id when empty

    def test_encode_with_id(self):
        msg = Message(type="permission.request", payload={"requestId": "abc123"}, id="abc123")
        data = msg.encode()
        parsed = json.loads(data.strip())
        assert parsed["id"] == "abc123"

    def test_decode_basic(self):
        line = b'{"type":"query.text_delta","payload":{"text":"hello"}}'
        msg = Message.decode(line)
        assert msg.type == "query.text_delta"
        assert msg.payload["text"] == "hello"
        assert msg.id == ""

    def test_decode_with_id(self):
        line = b'{"type":"permission.response","payload":{"decision":"allow"},"id":"req-1"}'
        msg = Message.decode(line)
        assert msg.type == "permission.response"
        assert msg.payload["decision"] == "allow"
        assert msg.id == "req-1"

    def test_roundtrip(self):
        original = Message(type="query.tool_use", payload={
            "toolUseId": "tu-1",
            "toolName": "Bash",
            "toolInput": {"command": "ls"},
            "toolPurpose": "listing files",
        })
        encoded = original.encode()
        decoded = Message.decode(encoded.strip())
        assert decoded.type == original.type
        assert decoded.payload == original.payload
        assert decoded.id == original.id

    def test_unicode_payload(self):
        msg = Message(type="query.text_delta", payload={"text": "你好世界 🌍"})
        encoded = msg.encode()
        decoded = Message.decode(encoded.strip())
        assert decoded.payload["text"] == "你好世界 🌍"

    def test_empty_payload(self):
        msg = Message(type="ping", payload={})
        encoded = msg.encode()
        decoded = Message.decode(encoded.strip())
        assert decoded.payload == {}

    def test_repr_truncation(self):
        long_payload = {"data": "x" * 200}
        msg = Message(type="test", payload=long_payload)
        r = repr(msg)
        assert len(r) < 300  # Should be truncated


class TestCoreToInk:
    """Test CoreToInk enum values."""

    def test_all_query_events_mapped(self):
        """Every QueryEvent type should have a corresponding CoreToInk entry."""
        query_event_types = {"text", "thinking", "tool_start", "tool_use", "tool_result", "error", "warning"}
        for event_type in query_event_types:
            assert event_type in QUERY_EVENT_MAP, f"Missing mapping for {event_type}"

    def test_session_types(self):
        assert CoreToInk.SESSION_READY.value == "session.ready"
        assert CoreToInk.SESSION_SHUTDOWN.value == "session.shutdown"

    def test_query_types(self):
        assert CoreToInk.QUERY_TEXT_DELTA.value == "query.text_delta"
        assert CoreToInk.QUERY_THINKING_DELTA.value == "query.thinking_delta"
        assert CoreToInk.QUERY_TOOL_START.value == "query.tool_start"
        assert CoreToInk.QUERY_TOOL_USE.value == "query.tool_use"
        assert CoreToInk.QUERY_TOOL_RESULT.value == "query.tool_result"
        assert CoreToInk.QUERY_ERROR.value == "query.error"
        assert CoreToInk.QUERY_WARNING.value == "query.warning"
        assert CoreToInk.QUERY_COMPLETE.value == "query.complete"

    def test_state_types(self):
        assert CoreToInk.STATE_UPDATE.value == "state.update"
        assert CoreToInk.STATE_SNAPSHOT.value == "state.snapshot"

    def test_permission_type(self):
        assert CoreToInk.PERMISSION_REQUEST.value == "permission.request"

    def test_heartbeat_type(self):
        assert CoreToInk.PING.value == "ping"


class TestInkToCore:
    """Test InkToCore enum values."""

    def test_user_input_types(self):
        assert InkToCore.USER_MESSAGE.value == "user.message"
        assert InkToCore.USER_INTERRUPT.value == "user.interrupt"
        assert InkToCore.USER_COMMAND.value == "user.command"

    def test_permission_response(self):
        assert InkToCore.PERMISSION_RESPONSE.value == "permission.response"

    def test_ui_types(self):
        assert InkToCore.UI_TOGGLE_THINKING.value == "ui.toggle_thinking"
        assert InkToCore.UI_RESIZE.value == "ui.resize"

    def test_lifecycle_types(self):
        assert InkToCore.READY.value == "ready"
        assert InkToCore.PONG.value == "pong"
        assert InkToCore.SHUTDOWN_ACK.value == "shutdown.ack"


class TestPermissionDecisionValue:
    """Test permission decision values."""

    def test_values(self):
        assert PermissionDecisionValue.ALLOW.value == "allow"
        assert PermissionDecisionValue.DENY.value == "deny"
        assert PermissionDecisionValue.ALWAYS_ALLOW.value == "always_allow"