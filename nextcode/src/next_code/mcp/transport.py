"""MCP transport layer — abstract base + stdio implementation.

Stdio is the most common MCP transport (90%+ of use cases). It spawns a local
subprocess and exchanges JSON-RPC messages over stdin/stdout.

Key design decisions:
- stderr is captured to a pipe and logged at DEBUG level, so MCP server debug
  output doesn't corrupt the TUI.
- Three-signal shutdown (SIGINT → SIGTERM → SIGKILL) gives the subprocess
  a chance to clean up while preventing zombie processes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


class MCPTransport(ABC):
    """Abstract base class for MCP transports."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish the transport connection."""

    @abstractmethod
    async def close(self) -> None:
        """Close the transport connection."""

    @abstractmethod
    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC message."""

    @abstractmethod
    async def receive(self) -> AsyncIterator[dict[str, Any]]:
        """Receive a stream of JSON-RPC messages."""


class StdioTransport(MCPTransport):
    """stdio transport — exchange JSON-RPC over subprocess stdin/stdout.

    Each message is a single newline-delimited JSON line (JSON-RPC over
    newline-delimited transport, as specified by MCP protocol).
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._command = command
        self._args = args or []
        self._env = env
        self._timeout = timeout
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Start the subprocess and set up stdin/stdout pipes."""
        env = dict(os.environ)
        if self._env:
            env.update(self._env)

        self._process = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # Drain stderr in background to prevent pipe blocking
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def close(self) -> None:
        """Three-signal shutdown: SIGINT → SIGTERM → SIGKILL.

        100ms + 400ms = 500ms total timeout.
        Gives the process a chance to clean up without hanging the CLI.
        """
        if self._stderr_task:
            self._stderr_task.cancel()
            self._stderr_task = None

        if not self._process or self._process.returncode is not None:
            return

        # 1. SIGINT — gentle interrupt
        try:
            self._process.send_signal(signal.SIGINT)
            await asyncio.wait_for(self._process.wait(), timeout=0.1)
            return
        except asyncio.TimeoutError:
            pass

        # 2. SIGTERM — stronger termination
        try:
            self._process.terminate()
            await asyncio.wait_for(self._process.wait(), timeout=0.4)
            return
        except asyncio.TimeoutError:
            pass

        # 3. SIGKILL — last resort, cannot be caught
        self._process.kill()
        await self._process.wait()

    async def send(self, message: dict[str, Any]) -> None:
        """Write a JSON-RPC message to subprocess stdin."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Transport not connected")
        data = json.dumps(message) + "\n"
        self._process.stdin.write(data.encode())
        await self._process.stdin.drain()

    async def receive(self) -> AsyncIterator[dict[str, Any]]:
        """Read JSON-RPC messages from subprocess stdout."""
        if not self._process or not self._process.stdout:
            raise RuntimeError("Transport not connected")

        while True:
            line = await self._process.stdout.readline()
            if not line:
                break  # EOF — process closed stdout
            try:
                yield json.loads(line.decode())
            except json.JSONDecodeError:
                logger.warning("Invalid JSON-RPC message from MCP server")

    async def _drain_stderr(self) -> None:
        """Background task: read stderr lines and log them at DEBUG level."""
        if not self._process or not self._process.stderr:
            return
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                logger.debug("MCP stderr: %s", line.decode().strip())
        except asyncio.CancelledError:
            pass
