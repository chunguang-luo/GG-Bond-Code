"""IPC transport — pipe-based IPC for Python ↔ Ink communication.

Provides bidirectional JSON-RPC 2.0 messaging over OS pipes.
Python creates two pipes, passes read ends to the Node.js child,
and communicates via the parent-side pipe ends.

Lifecycle:
    1. start(process, tx_fd, rx_fd) — begin I/O on the pipes
    2. send() / send_event() — write JSON-RPC notifications to tx pipe
    3. close() — clean shutdown, terminate child process
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from typing import Any, Callable, Awaitable

from .protocol import Message, decode_message

logger = logging.getLogger(__name__)


class IPCTransport:
    """Async pipe-based transport for IPC."""

    def __init__(self) -> None:
        self._popen: subprocess.Popen | None = None
        self._tx_writer: asyncio.StreamWriter | None = None  # Python → Node.js
        self._rx_reader: asyncio.StreamReader | None = None  # Node.js → Python
        self._rx_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._closed = False
        self._connected = asyncio.Event()
        self._message_handler: Callable[[Message], Awaitable[None]] | None = None

    @property
    def socket_path(self) -> str:
        return ""  # No socket path in pipe mode

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set() and not self._closed

    def on_message(self, handler: Callable[[Message], Awaitable[None]]) -> None:
        self._message_handler = handler

    async def start(
        self,
        process: subprocess.Popen,
        tx_fd: int,
        rx_fd: int,
    ) -> None:
        """Start transport using an already-running subprocess and pipe fds."""
        self._popen = process
        loop = asyncio.get_running_loop()

        # Set up StreamWriter for the tx pipe (Python → Node.js)
        tx_file = os.fdopen(tx_fd, "wb", buffering=0)
        tx_transport, tx_protocol = await loop.connect_write_pipe(
            lambda: asyncio.streams.FlowControlMixin(loop=loop),
            tx_file,
        )
        self._tx_writer = asyncio.StreamWriter(
            tx_transport, tx_protocol, None, loop=loop,
        )

        # Set up StreamReader for the rx pipe (Node.js → Python)
        rx_file = os.fdopen(rx_fd, "rb", buffering=0)
        self._rx_reader = asyncio.StreamReader(loop=loop)
        rx_protocol = asyncio.StreamReaderProtocol(self._rx_reader, loop=loop)
        await loop.connect_read_pipe(lambda: rx_protocol, rx_file)

        # Drain Node.js stderr in background
        if process.stderr is not None:
            self._stderr_task = asyncio.create_task(self._drain_stderr())

        self._connected.set()
        self._rx_task = asyncio.create_task(self._receive_loop())

        logger.info("IPC transport started (tx_fd=%d, rx_fd=%d)", tx_fd, rx_fd)

    async def wait_for_connection(self, timeout: float = 10.0) -> bool:
        return True  # Always already connected after start()

    async def send(self, msg: Message) -> None:
        """Send a message to the Ink process."""
        if self._tx_writer is None or self._closed:
            return

        try:
            data = msg.encode()
            self._tx_writer.write(data)
            await self._tx_writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            logger.warning("IPC: send failed: %s", e)
            self._connected.clear()

    async def send_event(
        self, msg_type: str, payload: dict[str, Any] | None = None, msg_id: str = "",
    ) -> None:
        await self.send(Message(type=msg_type, payload=payload or {}, id=msg_id))

    async def close(self) -> None:
        """Shut down transport and terminate child process."""
        self._closed = True
        self._connected.clear()

        # Cancel I/O tasks
        for task in (self._rx_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close tx writer
        if self._tx_writer is not None:
            try:
                self._tx_writer.close()
                await self._tx_writer.wait_closed()
            except (OSError, AttributeError, NotImplementedError):
                pass
            self._tx_writer = None
            self._rx_reader = None

        # Terminate child process
        if self._popen is not None and self._popen.poll() is None:
            try:
                self._popen.terminate()
                try:
                    await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(None, self._popen.wait),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    self._popen.kill()
            except ProcessLookupError:
                pass

        logger.info("IPC transport closed")

    # ── Internal ──────────────────────────────────────────────────────────

    async def _receive_loop(self) -> None:
        """Read and dispatch incoming messages from the rx pipe."""
        assert self._rx_reader is not None

        try:
            while not self._closed:
                line = await self._rx_reader.readline()
                if not line:
                    logger.info("IPC: Node.js rx pipe closed")
                    self._connected.clear()
                    break

                try:
                    data = decode_message(line.strip())
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("IPC: invalid message: %s", e)
                    continue

                # Convert JSON-RPC to legacy Message for bridge compatibility
                msg = Message(
                    type=data.get("method", data.get("type", "")),
                    payload=data.get("params", data.get("payload", {})),
                    id=str(data.get("id", "")),
                )

                if self._message_handler is not None:
                    try:
                        await self._message_handler(msg)
                    except Exception:
                        logger.exception("IPC: message handler error")
        except asyncio.CancelledError:
            pass
        except OSError:
            logger.info("IPC: receive pipe closed")
            self._connected.clear()
        except Exception:
            logger.exception("IPC: receive loop error")
            self._connected.clear()

    async def _drain_stderr(self) -> None:
        """Read Node.js stderr lines from Popen to prevent pipe blocking."""
        if self._popen is None or self._popen.stderr is None:
            return
        stderr = self._popen.stderr  # synchronous BufferedReader
        loop = asyncio.get_running_loop()
        try:
            while not self._closed:
                line = await loop.run_in_executor(None, stderr.readline)
                if not line:
                    break
                msg = line.strip()
                if msg:
                    logger.debug("Ink stderr: %s", msg)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
