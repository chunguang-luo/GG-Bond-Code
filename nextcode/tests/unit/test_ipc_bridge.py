"""Tests for IPC Bridge — QueryEvent to IPC message mapping and permission flow."""

import asyncio
import time
import uuid
import pytest
import pytest_asyncio

from next_code.ipc.bridge import IPCBridge
from next_code.ipc.protocol import (
    Message,
    CoreToInk,
    InkToCore,
    QUERY_EVENT_MAP,
    PermissionDecisionValue,
)
from next_code.ipc.transport import IPCTransport
from next_code.query import QueryEvent
from next_code.permissions.manager import PermissionDecision


class TestQueryEventMapping:
    """Test that QueryEvent types map to correct IPC message types."""

    def test_text_event(self):
        assert QUERY_EVENT_MAP["text"] == CoreToInk.QUERY_TEXT_DELTA

    def test_thinking_event(self):
        assert QUERY_EVENT_MAP["thinking"] == CoreToInk.QUERY_THINKING_DELTA

    def test_tool_start_event(self):
        assert QUERY_EVENT_MAP["tool_start"] == CoreToInk.QUERY_TOOL_START

    def test_tool_use_event(self):
        assert QUERY_EVENT_MAP["tool_use"] == CoreToInk.QUERY_TOOL_USE

    def test_tool_result_event(self):
        assert QUERY_EVENT_MAP["tool_result"] == CoreToInk.QUERY_TOOL_RESULT

    def test_error_event(self):
        assert QUERY_EVENT_MAP["error"] == CoreToInk.QUERY_ERROR

    def test_warning_event(self):
        assert QUERY_EVENT_MAP["warning"] == CoreToInk.QUERY_WARNING


class TestBridgeEventEmission:
    """Test IPCBridge._emit_event translates QueryEvents correctly."""

    @pytest_asyncio.fixture
    async def bridge_and_transport(self):
        """Create a bridge with a real transport for testing."""
        socket_path = f"/tmp/nextcode-bridge-{uuid.uuid4().hex[:8]}.sock"
        transport = IPCTransport(socket_path=socket_path)
        await transport.start()

        bridge = IPCBridge(transport=transport, model="deepseek-chat")

        # Accept a mock client
        client_messages: list[Message] = []

        async def mock_client():
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_unix_connection(socket_path)
            while True:
                try:
                    data = await asyncio.wait_for(reader.readline(), timeout=2.0)
                    if not data:
                        break
                    msg = Message.decode(data.strip())
                    client_messages.append(msg)
                except asyncio.TimeoutError:
                    break
            writer.close()
            await writer.wait_closed()

        client_task = asyncio.create_task(mock_client())
        connected = await transport.wait_for_connection(timeout=2.0)
        assert connected

        yield bridge, transport, client_messages

        await transport.close()
        try:
            await client_task
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_text_event(self, bridge_and_transport):
        bridge, transport, client_messages = bridge_and_transport
        event = QueryEvent(type="text", content="Hello world")
        await bridge._emit_event(event)
        await asyncio.sleep(0.2)

        assert len(client_messages) >= 1
        msg = client_messages[-1]
        assert msg.type == CoreToInk.QUERY_TEXT_DELTA.value
        assert msg.payload["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_thinking_event(self, bridge_and_transport):
        bridge, transport, client_messages = bridge_and_transport
        event = QueryEvent(type="thinking", content="Let me think...")
        await bridge._emit_event(event)
        await asyncio.sleep(0.2)

        msg = client_messages[-1]
        assert msg.type == CoreToInk.QUERY_THINKING_DELTA.value
        assert msg.payload["text"] == "Let me think..."

    @pytest.mark.asyncio
    async def test_tool_start_event(self, bridge_and_transport):
        bridge, transport, client_messages = bridge_and_transport
        event = QueryEvent(type="tool_start", tool_name="Bash", tool_use_id="tu-1")
        await bridge._emit_event(event)
        await asyncio.sleep(0.2)

        msg = client_messages[-1]
        assert msg.type == CoreToInk.QUERY_TOOL_START.value
        assert msg.payload["toolUseId"] == "tu-1"
        assert msg.payload["toolName"] == "Bash"

    @pytest.mark.asyncio
    async def test_tool_use_event(self, bridge_and_transport):
        bridge, transport, client_messages = bridge_and_transport
        event = QueryEvent(
            type="tool_use",
            tool_name="Read",
            tool_input={"file_path": "/tmp/test.py"},
            tool_use_id="tu-2",
            tool_purpose="Reading the file",
        )
        await bridge._emit_event(event)
        await asyncio.sleep(0.2)

        msg = client_messages[-1]
        assert msg.type == CoreToInk.QUERY_TOOL_USE.value
        assert msg.payload["toolName"] == "Read"
        assert msg.payload["toolInput"]["file_path"] == "/tmp/test.py"
        assert msg.payload["toolPurpose"] == "Reading the file"

    @pytest.mark.asyncio
    async def test_tool_result_event_with_elapsed(self, bridge_and_transport):
        bridge, transport, client_messages = bridge_and_transport
        # Simulate tool start time
        bridge._tool_start_times["tu-3"] = time.monotonic()

        event = QueryEvent(
            type="tool_result",
            tool_name="Bash",
            tool_result="file1.py\nfile2.py",
            tool_error=False,
            tool_use_id="tu-3",
        )
        await bridge._emit_event(event)
        await asyncio.sleep(0.2)

        msg = client_messages[-1]
        assert msg.type == CoreToInk.QUERY_TOOL_RESULT.value
        assert msg.payload["toolName"] == "Bash"
        assert msg.payload["toolResult"] == "file1.py\nfile2.py"
        assert msg.payload["toolError"] is False
        assert "elapsedMs" in msg.payload

    @pytest.mark.asyncio
    async def test_error_event(self, bridge_and_transport):
        bridge, transport, client_messages = bridge_and_transport
        event = QueryEvent(type="error", content="Something went wrong")
        await bridge._emit_event(event)
        await asyncio.sleep(0.2)

        msg = client_messages[-1]
        assert msg.type == CoreToInk.QUERY_ERROR.value
        assert msg.payload["content"] == "Something went wrong"

    @pytest.mark.asyncio
    async def test_warning_event(self, bridge_and_transport):
        bridge, transport, client_messages = bridge_and_transport
        event = QueryEvent(
            type="warning",
            content="Context usage high",
            metadata={"level": "warning", "percentUsed": 75},
        )
        await bridge._emit_event(event)
        await asyncio.sleep(0.2)

        msg = client_messages[-1]
        assert msg.type == CoreToInk.QUERY_WARNING.value
        assert msg.payload["content"] == "Context usage high"
        assert msg.payload["metadata"]["level"] == "warning"

    @pytest.mark.asyncio
    async def test_unmapped_event_type(self, bridge_and_transport):
        bridge, transport, client_messages = bridge_and_transport
        event = QueryEvent(type="unknown_type", content="???")
        # Should not raise, just log warning
        await bridge._emit_event(event)
        await asyncio.sleep(0.1)
        # No message should have been sent
        # (the only messages would be from setup if any)


class TestPermissionFlow:
    """Test IPC permission request-response flow."""

    @pytest.mark.asyncio
    async def test_permission_request_sent(self, tmp_path):
        """Bridge should send permission.request when ASK decision needed."""
        socket_path = f"/tmp/nextcode-perm-{uuid.uuid4().hex[:8]}.sock"
        transport = IPCTransport(socket_path=socket_path)
        await transport.start()

        bridge = IPCBridge(transport=transport, model="deepseek-chat")

        # Mock client that receives messages
        client_messages: list[Message] = []

        async def mock_client():
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_unix_connection(socket_path)
            while True:
                try:
                    data = await asyncio.wait_for(reader.readline(), timeout=5.0)
                    if not data:
                        break
                    msg = Message.decode(data.strip())
                    client_messages.append(msg)

                    # Auto-respond to permission requests
                    if msg.type == CoreToInk.PERMISSION_REQUEST.value:
                        response = Message(
                            type=InkToCore.PERMISSION_RESPONSE.value,
                            payload={
                                "requestId": msg.payload["requestId"],
                                "toolName": msg.payload.get("toolName", ""),
                                "params": msg.payload.get("params", {}),
                                "decision": PermissionDecisionValue.ALLOW.value,
                                "wildcard": False,
                            },
                        )
                        writer.write(response.encode())
                        await writer.drain()
                except asyncio.TimeoutError:
                    break
            writer.close()
            await writer.wait_closed()

        client_task = asyncio.create_task(mock_client())
        connected = await transport.wait_for_connection(timeout=2.0)
        assert connected

        # Trigger permission request
        decision = await bridge._ask_permission("Bash", {"command": "ls"})

        assert decision == PermissionDecision.ALLOW
        assert len(client_messages) >= 1
        perm_msgs = [m for m in client_messages if m.type == CoreToInk.PERMISSION_REQUEST.value]
        assert len(perm_msgs) == 1
        assert perm_msgs[0].payload["toolName"] == "Bash"
        assert perm_msgs[0].payload["params"]["command"] == "ls"

        await transport.close()
        try:
            await client_task
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_permission_deny(self, tmp_path):
        """Bridge should return DENY when Ink responds with deny."""
        socket_path = f"/tmp/nextcode-deny-{uuid.uuid4().hex[:8]}.sock"
        transport = IPCTransport(socket_path=socket_path)
        await transport.start()

        bridge = IPCBridge(transport=transport, model="deepseek-chat")

        async def mock_client():
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_unix_connection(socket_path)
            while True:
                try:
                    data = await asyncio.wait_for(reader.readline(), timeout=5.0)
                    if not data:
                        break
                    msg = Message.decode(data.strip())

                    if msg.type == CoreToInk.PERMISSION_REQUEST.value:
                        response = Message(
                            type=InkToCore.PERMISSION_RESPONSE.value,
                            payload={
                                "requestId": msg.payload["requestId"],
                                "toolName": msg.payload.get("toolName", ""),
                                "params": msg.payload.get("params", {}),
                                "decision": PermissionDecisionValue.DENY.value,
                                "wildcard": False,
                            },
                        )
                        writer.write(response.encode())
                        await writer.drain()
                except asyncio.TimeoutError:
                    break
            writer.close()
            await writer.wait_closed()

        client_task = asyncio.create_task(mock_client())
        connected = await transport.wait_for_connection(timeout=2.0)
        assert connected

        decision = await bridge._ask_permission("Bash", {"command": "rm -rf /"})
        assert decision == PermissionDecision.DENY

        await transport.close()
        try:
            await client_task
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_permission_timeout(self, tmp_path):
        """Bridge should return DENY when Ink doesn't respond within timeout."""
        socket_path = f"/tmp/nextcode-timeout-{uuid.uuid4().hex[:8]}.sock"
        transport = IPCTransport(socket_path=socket_path)
        await transport.start()

        bridge = IPCBridge(transport=transport, model="deepseek-chat")

        async def silent_client():
            """Client that receives but never responds."""
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_unix_connection(socket_path)
            await asyncio.sleep(5)  # Never respond
            writer.close()
            await writer.wait_closed()

        client_task = asyncio.create_task(silent_client())
        connected = await transport.wait_for_connection(timeout=2.0)
        assert connected

        # Should timeout and default to DENY
        decision = await bridge._ask_permission("Bash", {"command": "ls"})
        assert decision == PermissionDecision.DENY

        await transport.close()
        client_task.cancel()
        try:
            await client_task
        except asyncio.CancelledError:
            pass