"""Tests for IPC transport — socket server, message framing, JSON encode/decode."""

import asyncio
import json
import os
import uuid
import pytest
import pytest_asyncio

from gg_bond_code.ipc.transport import IPCTransport
from gg_bond_code.ipc.protocol import Message, CoreToInk, InkToCore


@pytest.fixture
def socket_path():
    """Provide a short temporary socket path (macOS has 104-byte limit)."""
    return f"/tmp/ggbond-test-{uuid.uuid4().hex[:8]}.sock"


class TestIPCTransport:
    """Test IPCTransport server lifecycle and messaging."""

    @pytest.mark.asyncio
    async def test_start_creates_socket(self, socket_path):
        t = IPCTransport(socket_path=socket_path)
        await t.start()
        assert os.path.exists(socket_path)
        await t.close()

    @pytest.mark.asyncio
    async def test_close_removes_socket(self, socket_path):
        t = IPCTransport(socket_path=socket_path)
        await t.start()
        assert os.path.exists(socket_path)
        await t.close()
        assert not os.path.exists(socket_path)

    @pytest.mark.asyncio
    async def test_initial_state(self, socket_path):
        t = IPCTransport(socket_path=socket_path)
        assert not t.is_connected
        await t.close()

    @pytest.mark.asyncio
    async def test_wait_for_connection_timeout(self, socket_path):
        t = IPCTransport(socket_path=socket_path)
        await t.start()
        # No client connecting, should timeout
        result = await t.wait_for_connection(timeout=0.5)
        assert result is False
        await t.close()

    @pytest.mark.asyncio
    async def test_send_without_connection(self, socket_path):
        """Sending when not connected should not raise."""
        t = IPCTransport(socket_path=socket_path)
        await t.start()
        msg = Message(type=CoreToInk.PING.value, payload={"timestamp": 123})
        await t.send(msg)  # Should not raise
        await t.close()

    @pytest.mark.asyncio
    async def test_send_event_convenience(self, socket_path):
        """send_event should work like send(Message(...))."""
        t = IPCTransport(socket_path=socket_path)
        await t.start()
        # Not connected, but shouldn't raise
        await t.send_event(CoreToInk.PING.value, {"timestamp": 456})
        await t.close()


class TestIPCTransportWithClient:
    """Test bidirectional communication with a mock client."""

    @pytest.mark.asyncio
    async def test_accept_connection(self, socket_path):
        """Server should accept a client connection."""
        transport = IPCTransport(socket_path=socket_path)
        await transport.start()

        async def client():
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_unix_connection(socket_path)
            await asyncio.sleep(0.1)
            writer.close()
            await writer.wait_closed()

        client_task = asyncio.create_task(client())
        connected = await transport.wait_for_connection(timeout=2.0)
        assert connected
        assert transport.is_connected

        await client_task
        await transport.close()

    @pytest.mark.asyncio
    async def test_receive_message(self, socket_path):
        """Server should receive messages from client."""
        transport = IPCTransport(socket_path=socket_path)
        received_messages: list[Message] = []

        async def handler(msg: Message):
            received_messages.append(msg)

        transport.on_message(handler)
        await transport.start()

        async def client():
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_unix_connection(socket_path)
            msg = Message(type=InkToCore.READY.value, payload={"version": "0.1.0"})
            writer.write(msg.encode())
            await writer.drain()
            await asyncio.sleep(0.3)
            writer.close()
            await writer.wait_closed()

        await transport.wait_for_connection(timeout=2.0)
        client_task = asyncio.create_task(client())
        await client_task

        # Give time for message processing
        await asyncio.sleep(0.2)

        assert len(received_messages) == 1
        assert received_messages[0].type == InkToCore.READY.value
        assert received_messages[0].payload["version"] == "0.1.0"

        await transport.close()

    @pytest.mark.asyncio
    async def test_send_message_to_client(self, socket_path):
        """Server should be able to send messages to client."""
        transport = IPCTransport(socket_path=socket_path)
        await transport.start()

        client_received: list[bytes] = []

        async def client():
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_unix_connection(socket_path)
            # Wait for server message
            data = await asyncio.wait_for(reader.readline(), timeout=2.0)
            client_received.append(data)
            writer.close()
            await writer.wait_closed()

        client_task = asyncio.create_task(client())
        connected = await transport.wait_for_connection(timeout=2.0)
        assert connected

        # Send a message
        await transport.send_event(CoreToInk.SESSION_READY.value, {
            "model": "deepseek-chat",
            "cwd": "/tmp",
            "projectRoot": "/tmp",
        })

        await client_task

        assert len(client_received) == 1
        parsed = json.loads(client_received[0].strip())
        assert parsed["type"] == "session.ready"
        assert parsed["payload"]["model"] == "deepseek-chat"

        await transport.close()

    @pytest.mark.asyncio
    async def test_multiple_messages(self, socket_path):
        """Server should handle multiple messages in sequence."""
        transport = IPCTransport(socket_path=socket_path)
        received_messages: list[Message] = []

        transport.on_message(lambda msg: received_messages.append(msg))
        await transport.start()

        async def client():
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_unix_connection(socket_path)
            for i in range(5):
                msg = Message(
                    type=InkToCore.USER_MESSAGE.value,
                    payload={"text": f"message {i}"},
                )
                writer.write(msg.encode())
                await writer.drain()
            await asyncio.sleep(0.3)
            writer.close()
            await writer.wait_closed()

        await transport.wait_for_connection(timeout=2.0)
        client_task = asyncio.create_task(client())
        await client_task
        await asyncio.sleep(0.3)

        assert len(received_messages) == 5
        for i, msg in enumerate(received_messages):
            assert msg.type == InkToCore.USER_MESSAGE.value
            assert msg.payload["text"] == f"message {i}"

        await transport.close()

    @pytest.mark.asyncio
    async def test_stale_socket_cleanup(self):
        """Transport should clean up stale socket files on start."""
        socket_path = f"/tmp/ggbond-stale-{uuid.uuid4().hex[:8]}.sock"
        # Create a stale socket file
        with open(socket_path, "w") as f:
            f.write("stale")

        transport = IPCTransport(socket_path=socket_path)
        await transport.start()
        # Socket should have been replaced
        assert os.path.exists(socket_path)
        await transport.close()

    @pytest.mark.asyncio
    async def test_connection_close_detection(self, socket_path):
        """Server should detect when client disconnects."""
        transport = IPCTransport(socket_path=socket_path)
        await transport.start()

        async def client():
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_unix_connection(socket_path)
            await asyncio.sleep(0.1)
            writer.close()
            await writer.wait_closed()

        await transport.wait_for_connection(timeout=2.0)
        client_task = asyncio.create_task(client())
        await client_task

        # Wait for disconnect detection
        await asyncio.sleep(0.5)
        assert not transport.is_connected

        await transport.close()