"""MCP client — connection management, JSON-RPC calls, tool discovery.

The client sits between the transport layer and the connection manager.
It handles:
- MCP initialize handshake (protocol version negotiation)
- JSON-RPC request/response correlation (id-based)
- Tool discovery (tools/list) and invocation (tools/call)
- Prompt and resource discovery
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .types import ScopedMcpServerConfig
from .transport import MCPTransport

logger = logging.getLogger(__name__)

# Connection timeout for initialize handshake (seconds)
CONNECTION_TIMEOUT_S = 30.0


class MCPClient:
    """MCP client — manages JSON-RPC communication with an MCP server."""

    def __init__(self, name: str, transport: MCPTransport) -> None:
        self.name = name
        self._transport = transport
        self._request_id = 0
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._recv_task: asyncio.Task | None = None
        self._connected = False
        self._on_close: Any | None = None  # Optional callback for unexpected close

    async def connect(self) -> dict[str, Any]:
        """Establish connection and perform initialize handshake.

        Returns server info (name, version, capabilities).
        """
        await self._transport.connect()
        self._recv_task = asyncio.create_task(self._recv_loop())

        # MCP initialize handshake
        result = await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "NextCode", "version": "0.1.0"},
        })

        # Send initialized notification (completes the handshake)
        await self._notify("notifications/initialized", {})

        self._connected = True
        return result

    async def close(self) -> None:
        """Close the connection and cancel pending requests."""
        self._connected = False
        if self._recv_task:
            self._recv_task.cancel()
            self._recv_task = None

        # Reject all pending futures
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("Connection closed"))
        self._pending.clear()

        await self._transport.close()

    async def list_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by the server."""
        result = await self._request("tools/list", {})
        return result.get("tools", [])

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        """Invoke an MCP tool on the server."""
        result = await self._request("tools/call", {
            "name": tool_name,
            "arguments": args,
        })
        return result.get("content", [])

    async def list_prompts(self) -> list[dict[str, Any]]:
        """Get the list of prompts provided by the server."""
        result = await self._request("prompts/list", {})
        return result.get("prompts", [])

    async def get_prompt(self, name: str, args: dict[str, str] | None = None) -> dict[str, Any]:
        """Get an MCP prompt."""
        params: dict[str, Any] = {"name": name}
        if args:
            params["arguments"] = args
        return await self._request("prompts/get", params)

    async def list_resources(self) -> list[dict[str, Any]]:
        """Get the list of resources provided by the server."""
        result = await self._request("resources/list", {})
        return result.get("resources", [])

    # ── Internal Methods ───────────────────────────────────────

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        self._request_id += 1
        request_id = self._request_id

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[str(request_id)] = future

        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        await self._transport.send(message)

        return await asyncio.wait_for(future, timeout=CONNECTION_TIMEOUT_S)

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await self._transport.send(message)

    async def _recv_loop(self) -> None:
        """Continuously receive messages and dispatch to pending futures."""
        try:
            async for message in self._transport.receive():
                if "id" in message:
                    # Response to a previous request
                    request_id = str(message["id"])
                    future = self._pending.pop(request_id, None)
                    if future and not future.done():
                        if "error" in message:
                            error_msg = message["error"]
                            if isinstance(error_msg, dict):
                                error_msg = error_msg.get("message", str(error_msg))
                            future.set_exception(RuntimeError(f"MCP error: {error_msg}"))
                        else:
                            future.set_result(message.get("result", {}))
                elif "method" in message:
                    # Server-initiated notification
                    logger.debug(
                        "MCP notification from %s: %s", self.name, message["method"]
                    )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("MCP recv loop error for %s: %s", self.name, e)
            # If we were connected and the loop dies unexpectedly, notify the manager
            if self._connected and self._on_close:
                self._connected = False
                self._on_close()
