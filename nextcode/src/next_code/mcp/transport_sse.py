"""SSE transport for MCP — GET for receiving, POST for sending.

MCP's SSE transport (legacy, pre-Streamable HTTP):
- Client opens a GET request to the SSE endpoint to receive messages.
- Server sends messages as SSE events with event types:
  - "endpoint": contains the POST endpoint URL for sending messages
  - "message": contains a JSON-RPC message
- Client sends JSON-RPC requests via POST to the endpoint URL from the
  "endpoint" event.

This transport is maintained for backward compatibility with MCP servers
that only support the SSE transport (not Streamable HTTP).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx

from .transport import MCPTransport

logger = logging.getLogger(__name__)

# Request timeout for POST requests
SSE_REQUEST_TIMEOUT_S = 60.0


class SSETransport(MCPTransport):
    """SSE transport — GET for receiving, POST for sending.

    Connection flow:
    1. GET the SSE endpoint URL to open the event stream.
    2. Wait for the "endpoint" event which provides the POST URL.
    3. Send JSON-RPC requests via POST to that URL.
    4. Receive responses and notifications via the SSE stream.

    SSE event format:
    - event: endpoint\\ndata: /mcp/post-url\\n\\n
    - event: message\\ndata: {"jsonrpc":"2.0",...}\\n\\n
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = SSE_REQUEST_TIMEOUT_S,
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._timeout = timeout
        self._post_url: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._message_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._sse_task: asyncio.Task | None = None
        self._connected = False

    async def connect(self) -> None:
        """Open the SSE stream and wait for the endpoint event."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=self._timeout, read=None),
        )

        # Start SSE stream as background task
        self._sse_task = asyncio.create_task(self._listen_sse_stream())
        self._connected = True

        # Wait for the endpoint event (with timeout)
        try:
            await asyncio.wait_for(self._wait_for_endpoint(), timeout=30.0)
        except asyncio.TimeoutError:
            await self.close()
            raise RuntimeError("SSE transport: timed out waiting for endpoint event")

    async def close(self) -> None:
        """Close the SSE stream and HTTP client."""
        self._connected = False

        if self._sse_task:
            self._sse_task.cancel()
            self._sse_task = None

        # Signal EOF
        await self._message_queue.put(None)

        if self._client:
            await self._client.aclose()
            self._client = None
        self._post_url = None

    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC message via POST to the SSE endpoint."""
        if not self._client or not self._connected:
            raise RuntimeError("Transport not connected")
        if not self._post_url:
            raise RuntimeError("SSE transport: no POST endpoint received yet")

        headers = dict(self._headers)
        headers["Content-Type"] = "application/json"

        try:
            response = await self._client.post(
                self._post_url,
                json=message,
                headers=headers,
            )
            response.raise_for_status()

            # Some servers return the response inline instead of via SSE
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type and response.text.strip():
                result = _try_parse_json(response.text)
                if result:
                    await self._message_queue.put(result)

        except httpx.HTTPStatusError as e:
            # 401 = needs auth
            if e.response.status_code == 401:
                raise NeedsAuthError("MCP server requires authentication")
            raise RuntimeError(f"MCP SSE POST error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise RuntimeError(f"MCP SSE POST failed: {e}") from e

    async def receive(self) -> AsyncIterator[dict[str, Any]]:
        """Yield messages from the queue (populated by the SSE listener)."""
        while self._connected:
            message = await self._message_queue.get()
            if message is None:
                break
            yield message

    async def _listen_sse_stream(self) -> None:
        """Background task: listen to the SSE stream and queue messages."""
        headers = dict(self._headers)
        headers["Accept"] = "text/event-stream"

        try:
            async with self._client.stream("GET", self._url, headers=headers) as response:
                response.raise_for_status()

                event_type = ""
                data_parts: list[str] = []

                async for line in response.aiter_lines():
                    if not self._connected:
                        break

                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                    elif line.startswith("data: "):
                        data_parts.append(line[6:])
                    elif line == "" and data_parts:
                        # Event boundary
                        raw = "\n".join(data_parts)

                        if event_type == "endpoint":
                            # Resolve relative URL against base URL
                            self._post_url = _resolve_url(self._url, raw.strip())
                            logger.debug("SSE endpoint: %s", self._post_url)
                        elif event_type == "message":
                            try:
                                message = json.loads(raw)
                                await self._message_queue.put(message)
                            except json.JSONDecodeError:
                                logger.warning("Invalid SSE message: %s", raw[:200])

                        event_type = ""
                        data_parts = []

        except asyncio.CancelledError:
            pass
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.debug("SSE stream: server requires auth (401)")
                # Don't raise — let the client detect needs-auth via a failed request
            else:
                logger.warning("SSE stream HTTP error: %s", e)
        except Exception as e:
            if self._connected:
                logger.warning("SSE stream error: %s", e)

    async def _wait_for_endpoint(self) -> None:
        """Wait until the POST endpoint URL has been received."""
        while self._post_url is None and self._connected:
            await asyncio.sleep(0.05)
        if not self._post_url:
            raise RuntimeError("SSE transport: failed to get POST endpoint")


class NeedsAuthError(Exception):
    """Raised when the MCP server requires authentication (HTTP 401)."""


def _resolve_url(base_url: str, path: str) -> str:
    """Resolve a possibly-relative URL against a base URL.

    Handles cases where the SSE endpoint event provides a relative path
    like "/mcp/messages" instead of a full URL.
    """
    if path.startswith(("http://", "https://")):
        return path

    # Parse base URL and combine with relative path
    from urllib.parse import urljoin
    return urljoin(base_url, path)


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Try to parse JSON, return None on failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
