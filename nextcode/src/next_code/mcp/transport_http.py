"""Streamable HTTP transport for MCP — POST to send, SSE stream to receive.

MCP's Streamable HTTP transport (protocol version 2025-03-26+):
- Client sends JSON-RPC requests via POST to the server URL.
- Server responds with either a single JSON-RPC response or an SSE stream
  of responses (for notifications and streaming results).
- GET requests open a standalone SSE stream for server-initiated messages.
- Session management via Mcp-Session-Id header.

Why not use a simpler request/response pattern:
MCP servers can send server-initiated notifications (e.g., tools/list_changed)
and streaming tool results. The SSE response format enables this without
requiring WebSocket or long-polling.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx

from .transport import MCPTransport

logger = logging.getLogger(__name__)

# Request timeout for non-GET requests (60 seconds).
# GET requests (SSE streams) have NO timeout — they are long-lived.
MCP_REQUEST_TIMEOUT_S = 60.0

# Session expiry detection: HTTP 404 + JSON-RPC error code -32001
SESSION_EXPIRED_HTTP_STATUS = 404
SESSION_EXPIRED_JSONRPC_CODE = -32001


class StreamableHTTPTransport(MCPTransport):
    """Streamable HTTP transport — POST to send, SSE stream to receive.

    Connection flow:
    1. POST initialize request to the server URL.
    2. Server responds with SSE stream containing initialize result.
    3. Extract Mcp-Session-Id from response headers for subsequent requests.
    4. For each request, POST to the same URL with session ID header.
    5. Server may respond with SSE stream or single JSON response.

    Session expiry:
    If server returns 404 with JSON-RPC error -32001, the session has expired.
    The transport signals this by raising SessionExpiredError, which the
    connection manager handles with immediate reconnect (no backoff count).
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = MCP_REQUEST_TIMEOUT_S,
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._timeout = timeout
        self._session_id: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._message_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._background_tasks: list[asyncio.Task] = []
        self._connected = False

    async def connect(self) -> None:
        """Initialize the HTTP client (no network call yet — connect happens on first request)."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout=self._timeout,
                # No read timeout — SSE streams are long-lived
                read=None,
            ),
        )
        self._connected = True

    async def close(self) -> None:
        """Close the HTTP client and cancel background tasks."""
        self._connected = False

        # Cancel background SSE listeners
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()

        # Signal EOF to message queue
        await self._message_queue.put(None)

        if self._client:
            await self._client.aclose()
            self._client = None
        self._session_id = None

    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC message via POST.

        The server may respond with:
        - A single JSON-RPC response (Content-Type: application/json)
        - An SSE stream of messages (Content-Type: text/event-stream)

        Both cases are handled — SSE responses are parsed in a background task.
        """
        if not self._client or not self._connected:
            raise RuntimeError("Transport not connected")

        headers = dict(self._headers)
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json, text/event-stream"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        try:
            response = await self._client.post(
                self._url,
                json=message,
                headers=headers,
            )

            # Check for session expiry
            if response.status_code == SESSION_EXPIRED_HTTP_STATUS:
                body = _try_parse_json(response.text)
                if body and body.get("error", {}).get("code") == SESSION_EXPIRED_JSONRPC_CODE:
                    raise SessionExpiredError("MCP session expired")

            response.raise_for_status()

            # Capture session ID from response
            new_session_id = response.headers.get("mcp-session-id")
            if new_session_id:
                self._session_id = new_session_id

            # Handle response based on Content-Type
            content_type = response.headers.get("content-type", "")

            if "text/event-stream" in content_type:
                # SSE response — parse events in background
                task = asyncio.create_task(self._parse_sse_response(response.text))
                self._background_tasks.append(task)
            elif "application/json" in content_type:
                # Single JSON response
                result = _try_parse_json(response.text)
                if result:
                    await self._message_queue.put(result)

        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"MCP HTTP error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise RuntimeError(f"MCP HTTP request failed: {e}") from e

    async def receive(self) -> AsyncIterator[dict[str, Any]]:
        """Yield messages from the queue (populated by send() and background listeners)."""
        while self._connected:
            message = await self._message_queue.get()
            if message is None:
                break  # EOF signal
            yield message

    async def open_notification_stream(self) -> None:
        """Open a standalone GET SSE stream for server-initiated messages.

        Per MCP spec, the client MAY open a GET request to receive
        server-initiated notifications. This is optional and started
        after the initialize handshake completes.
        """
        if not self._client or not self._connected:
            return

        headers = dict(self._headers)
        headers["Accept"] = "text/event-stream"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        task = asyncio.create_task(self._get_sse_stream(headers))
        self._background_tasks.append(task)

    async def _get_sse_stream(self, headers: dict[str, str]) -> None:
        """Background task: GET SSE stream for server-initiated messages."""
        try:
            async with self._client.stream("GET", self._url, headers=headers) as response:
                response.raise_for_status()
                async for event in _parse_sse_lines(response.aiter_lines()):
                    if isinstance(event, dict):
                        await self._message_queue.put(event)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("MCP HTTP notification stream ended: %s", e)

    async def _parse_sse_response(self, text: str) -> None:
        """Parse SSE events from a POST response body and queue them."""
        try:
            for event in _parse_sse_text(text):
                if isinstance(event, dict):
                    await self._message_queue.put(event)
        except Exception as e:
            logger.warning("Failed to parse SSE response: %s", e)


class SessionExpiredError(Exception):
    """Raised when the MCP server session has expired (404 + -32001).

    This is a special error category that triggers immediate reconnect
    without counting against the terminal error limit.
    """


# ── SSE Parsing Helpers ────────────────────────────────────────

def _parse_sse_text(text: str) -> list[dict[str, Any]]:
    """Parse SSE events from a complete text response."""
    events: list[dict[str, Any]] = []
    current_data: list[str] = []

    for line in text.split("\n"):
        if line.startswith("data: "):
            current_data.append(line[6:])
        elif line == "" and current_data:
            # Empty line = event boundary
            raw = "\n".join(current_data)
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                logger.warning("Invalid SSE data: %s", raw[:200])
            current_data = []

    # Handle last event without trailing newline
    if current_data:
        raw = "\n".join(current_data)
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning("Invalid SSE data: %s", raw[:200])

    return events


async def _parse_sse_lines(line_iter: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Parse SSE events from an async line iterator (for streaming GET)."""
    current_data: list[str] = []

    async for line in line_iter:
        if line.startswith("data: "):
            current_data.append(line[6:])
        elif line == "" and current_data:
            raw = "\n".join(current_data)
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Invalid SSE data: %s", raw[:200])
            current_data = []

    # Handle last event
    if current_data:
        raw = "\n".join(current_data)
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid SSE data: %s", raw[:200])


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Try to parse JSON, return None on failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
