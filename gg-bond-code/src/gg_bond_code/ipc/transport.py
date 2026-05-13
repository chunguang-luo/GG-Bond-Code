"""IPC transport — Unix domain socket server for Python ↔ Ink communication.

Provides bidirectional JSON-line messaging over a Unix domain socket.
Python creates the socket server, Ink connects as a client.

Lifecycle:
    1. start() — create + bind + listen on socket
    2. wait_for_connection() — accept a single client
    3. send() / receive() — bidirectional message passing
    4. close() — clean shutdown, remove socket file
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Awaitable

from .protocol import Message

logger = logging.getLogger(__name__)


class IPCTransport:
    """Async Unix domain socket transport for IPC messages."""

    def __init__(self, socket_path: str | None = None) -> None:
        if socket_path is None:
            socket_path = f"/tmp/ggbond-ipc-{os.getpid()}.sock"
        self.socket_path = socket_path
        self._server: asyncio.Server | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = asyncio.Event()
        self._closed = False
        self._message_handler: Callable[[Message], Awaitable[None]] | None = None
        self._receive_task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set() and not self._closed

    def on_message(self, handler: Callable[[Message], Awaitable[None]]) -> None:
        """Register an async message handler for incoming Ink messages."""
        self._message_handler = handler

    async def start(self) -> None:
        """Create and start the Unix socket server."""
        # Remove stale socket file
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=self.socket_path,
        )
        logger.info("IPC server listening on %s", self.socket_path)

    async def wait_for_connection(self, timeout: float = 10.0) -> bool:
        """Wait for the Ink process to connect.

        Returns True if connected within timeout, False otherwise.
        """
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning("IPC: no connection within %.1fs", timeout)
            return False

    async def send(self, msg: Message) -> None:
        """Send a message to the connected Ink process."""
        if self._writer is None or self._closed:
            logger.warning("IPC: cannot send, not connected")
            return

        try:
            data = msg.encode()
            self._writer.write(data)
            await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            logger.warning("IPC: send failed: %s", e)
            self._connected.clear()

    async def send_event(self, msg_type: str, payload: dict[str, Any] | None = None, msg_id: str = "") -> None:
        """Convenience: send a message by type and payload."""
        await self.send(Message(type=msg_type, payload=payload or {}, id=msg_id))

    async def close(self) -> None:
        """Shut down the transport cleanly."""
        self._closed = True
        self._connected.clear()

        # Cancel receive task
        if self._receive_task is not None and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        # Close writer
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
            self._reader = None

        # Close server
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        # Remove socket file
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

        logger.info("IPC transport closed")

    # ── Internal ──────────────────────────────────────────────────────────

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a new connection from the Ink process."""
        if self._writer is not None:
            logger.warning("IPC: rejecting extra connection")
            writer.close()
            return

        self._reader = reader
        self._writer = writer
        self._connected.set()
        logger.info("IPC: Ink process connected")

        # Start receiving messages
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        """Read and dispatch incoming messages."""
        assert self._reader is not None

        try:
            while not self._closed:
                line = await self._reader.readline()
                if not line:
                    # Connection closed by Ink
                    logger.info("IPC: connection closed by Ink")
                    self._connected.clear()
                    break

                try:
                    msg = Message.decode(line.strip())
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("IPC: invalid message: %s", e)
                    continue

                # Dispatch to handler
                if self._message_handler is not None:
                    try:
                        await self._message_handler(msg)
                    except Exception:
                        logger.exception("IPC: message handler error")
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            logger.info("IPC: connection reset by Ink")
            self._connected.clear()
        except Exception:
            logger.exception("IPC: receive loop error")
            self._connected.clear()
