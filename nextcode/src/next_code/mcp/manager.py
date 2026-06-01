"""MCP connection manager — global lifecycle management.

Responsibilities:
1. Config loading and merging (two-phase)
2. Concurrent connection scheduling (local/remote separate concurrency)
3. Tool discovery and registration into ToolRegistry
4. Connection lifecycle (reconnect with exponential backoff, cleanup)
5. Auth detection (needs-auth state, 15-minute cache)
6. Status reporting

Local servers (stdio) use LOCAL_CONCURRENCY=3 to avoid resource contention.
Remote servers (HTTP/SSE/WS) use REMOTE_CONCURRENCY=20 since they're just
network I/O.

Auto-reconnect:
- Exponential backoff: 1s → 2s → 4s → 8s → 16s (max 30s).
- 5 attempts before giving up.
- Session expiry (404 + -32001) triggers immediate reconnect (no backoff count).
- Terminal errors (ECONNRESET, etc.) count toward error limit before reconnect.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .types import (
    ScopedMcpServerConfig, StdioServerConfig, SSEServerConfig,
    HTTPServerConfig, WebSocketServerConfig,
    MCPServerConnection, ConnectedMCPServer, FailedMCPServer,
    NeedsAuthMCPServer, PendingMCPServer,
)
from .client import MCPClient
from .transport import StdioTransport
from .transport_http import StreamableHTTPTransport, SessionExpiredError
from .transport_sse import SSETransport, NeedsAuthError
from .auth import OAuthClient
from .headers_helper import execute_headers_helper, should_run_headers_helper
from .tool_proxy import MCPToolProxy
from .config import get_all_mcp_configs
from .naming import is_mcp_tool_name
from ..tools.base import ToolRegistry

logger = logging.getLogger(__name__)

# Concurrency limits
LOCAL_CONCURRENCY = 3    # stdio servers (subprocess — resource-heavy)
REMOTE_CONCURRENCY = 20  # HTTP/SSE/WS servers (network I/O — lightweight)

# Reconnect parameters
MAX_RECONNECT_ATTEMPTS = 5
INITIAL_BACKOFF_MS = 1000   # 1 second
MAX_BACKOFF_MS = 30000      # 30 seconds

# Terminal connection errors that count toward error limit
_TERMINAL_ERROR_PATTERNS = (
    "ECONNRESET", "ETIMEDOUT", "EPIPE", "EHOSTUNREACH",
    "ECONNREFUSED", "Body Timeout Error", "terminated",
    "SSE stream disconnected", "Failed to reconnect SSE stream",
    "Maximum reconnection attempts",
)
MAX_ERRORS_BEFORE_RECONNECT = 3


class MCPConnectionManager:
    """Global MCP connection manager.

    Usage:
        manager = MCPConnectionManager(registry)
        await manager.initialize()      # Phase 1: load config + connect
        ...
        await manager.shutdown()        # Close all connections
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._connections: dict[str, MCPServerConnection] = {}
        self._clients: dict[str, MCPClient] = {}
        self._mcp_tools: dict[str, MCPToolProxy] = {}  # fqn -> proxy
        self._oauth_clients: dict[str, OAuthClient] = {}
        self._consecutive_errors: dict[str, int] = {}
        self._reconnect_tasks: dict[str, asyncio.Task] = {}

    async def initialize(self, dynamic_configs: dict[str, Any] | None = None) -> None:
        """Phase 1: Load configs and connect to all MCP servers.

        Does NOT block on slow servers — connection happens in the background
        and tools are registered as each server comes online.
        """
        configs = get_all_mcp_configs(dynamic_configs)

        if not configs:
            logger.debug("No MCP servers configured")
            return

        # Split into local (stdio) vs remote groups
        local_servers: dict[str, ScopedMcpServerConfig] = {}
        remote_servers: dict[str, ScopedMcpServerConfig] = {}
        for name, scoped in configs.items():
            config = scoped.config
            # Check needs-auth cache for remote servers
            if isinstance(config, (SSEServerConfig, HTTPServerConfig)):
                if config.oauth:
                    oauth_client = OAuthClient(name, config.oauth)
                    self._oauth_clients[name] = oauth_client
                    # Discover endpoints and check cache
                    await oauth_client.discover_endpoints(config.url)
                    if oauth_client.is_needs_auth_cached() or oauth_client.has_discovery_but_no_token():
                        self._connections[name] = NeedsAuthMCPServer(
                            name=name, config=scoped,
                        )
                        oauth_client.mark_needs_auth()
                        logger.debug("MCP server '%s': needs auth (cached)", name)
                        continue

            if isinstance(config, StdioServerConfig):
                local_servers[name] = scoped
            else:
                remote_servers[name] = scoped

            # Mark as pending
            self._connections[name] = PendingMCPServer(name=name, config=scoped)

        # Both groups connect in parallel with separate concurrency limits
        await asyncio.gather(
            self._connect_batch(local_servers, LOCAL_CONCURRENCY),
            self._connect_batch(remote_servers, REMOTE_CONCURRENCY),
        )

    async def _connect_batch(
        self,
        servers: dict[str, ScopedMcpServerConfig],
        concurrency: int,
    ) -> None:
        """Connect a batch of servers with semaphore-based concurrency."""
        if not servers:
            return

        sem = asyncio.Semaphore(concurrency)

        async def _connect_one(name: str, scoped: ScopedMcpServerConfig) -> None:
            async with sem:
                await self._connect_server(name, scoped)

        tasks = [
            asyncio.create_task(_connect_one(name, scoped))
            for name, scoped in servers.items()
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _connect_server(self, name: str, scoped: ScopedMcpServerConfig) -> None:
        """Connect a single MCP server."""
        config = scoped.config
        transport = None

        try:
            # Build auth headers for remote servers
            extra_headers: dict[str, str] = {}

            if isinstance(config, (SSEServerConfig, HTTPServerConfig)):
                # Static headers from config
                extra_headers = dict(config.headers)

                # Dynamic headers from headersHelper
                if config.headers_helper:
                    scope_name = scoped.scope.value if scoped.scope else "unknown"
                    if should_run_headers_helper(config.headers_helper, scope_name):
                        try:
                            dynamic_headers = await execute_headers_helper(
                                config.headers_helper, name, config.url,
                            )
                            extra_headers.update(dynamic_headers)
                        except Exception as e:
                            logger.warning("headersHelper failed for '%s': %s", name, e)

                # OAuth headers
                oauth_client = self._oauth_clients.get(name)
                if oauth_client:
                    tokens = await oauth_client.load_tokens()
                    if tokens:
                        # Check if token needs refresh
                        expires_at = tokens.get("expires_at", 0)
                        if expires_at and expires_at < asyncio.get_event_loop().time() + 30:
                            refresh_token = tokens.get("refresh_token")
                            if refresh_token:
                                try:
                                    tokens = await oauth_client.refresh_tokens(refresh_token)
                                except Exception as e:
                                    logger.warning("Token refresh failed for '%s': %s", name, e)

                        extra_headers.update(oauth_client.auth_headers(tokens))

            # Create transport based on config type
            if isinstance(config, StdioServerConfig):
                transport = StdioTransport(
                    command=config.command,
                    args=config.args,
                    env=config.env,
                )
            elif isinstance(config, HTTPServerConfig):
                transport = StreamableHTTPTransport(
                    url=config.url,
                    headers=extra_headers,
                )
            elif isinstance(config, SSEServerConfig):
                transport = SSETransport(
                    url=config.url,
                    headers=extra_headers,
                )
            else:
                # WebSocket and SDK: Phase 3
                self._connections[name] = FailedMCPServer(
                    name=name, config=scoped,
                    error=f"Transport type '{config.type}' not yet implemented",
                )
                logger.warning(
                    "MCP server '%s': transport type '%s' not yet implemented",
                    name, config.type,
                )
                return

            # Create client and perform initialize handshake
            client = MCPClient(name, transport)

            # Set up close callback for auto-reconnect
            client._on_close = lambda n=name, s=scoped: self._on_client_closed(n, s)

            server_info = await client.connect()

            # For HTTP transport, open notification stream after initialize
            if isinstance(transport, StreamableHTTPTransport):
                await transport.open_notification_stream()

            # Connection succeeded
            self._clients[name] = client
            self._connections[name] = ConnectedMCPServer(
                name=name,
                config=scoped,
                server_info=server_info.get("serverInfo"),
                capabilities=server_info.get("capabilities", {}),
            )
            self._consecutive_errors.pop(name, None)  # Reset error count

            # Discover tools and register them
            await self._discover_and_register_tools(name, client)

            logger.info("MCP server '%s' connected successfully", name)

        except NeedsAuthError:
            # Server requires authentication
            self._connections[name] = NeedsAuthMCPServer(
                name=name, config=scoped,
            )
            oauth_client = self._oauth_clients.get(name)
            if oauth_client:
                oauth_client.mark_needs_auth()
            logger.warning("MCP server '%s' requires authentication", name)

        except SessionExpiredError:
            # Session expired — immediate reconnect (no backoff count)
            logger.info("MCP server '%s': session expired, reconnecting", name)
            await self._reconnect_server(name, scoped, is_session_expired=True)

        except asyncio.TimeoutError:
            self._handle_connection_error(
                name, scoped, "Connection timed out after 30s", is_timeout=True,
            )

        except Exception as e:
            error_msg = str(e)
            is_terminal = any(pattern in error_msg for pattern in _TERMINAL_ERROR_PATTERNS)
            self._handle_connection_error(name, scoped, error_msg, is_terminal=is_terminal)

    def _handle_connection_error(
        self,
        name: str,
        scoped: ScopedMcpServerConfig,
        error: str,
        *,
        is_terminal: bool = False,
        is_timeout: bool = False,
    ) -> None:
        """Handle a connection error, potentially triggering reconnect."""
        if is_terminal or is_timeout:
            self._consecutive_errors[name] = self._consecutive_errors.get(name, 0) + 1

            if self._consecutive_errors[name] >= MAX_ERRORS_BEFORE_RECONNECT:
                logger.warning(
                    "MCP server '%s': %d consecutive errors, marking failed: %s",
                    name, self._consecutive_errors[name], error,
                )
                self._connections[name] = FailedMCPServer(
                    name=name, config=scoped, error=error,
                )
                # Start reconnect in background
                reconnect_task = asyncio.create_task(
                    self._reconnect_server(name, scoped)
                )
                self._reconnect_tasks[name] = reconnect_task
            else:
                logger.warning(
                    "MCP server '%s' connection error (%d/%d): %s",
                    name, self._consecutive_errors[name], MAX_ERRORS_BEFORE_RECONNECT, error,
                )
                self._connections[name] = FailedMCPServer(
                    name=name, config=scoped, error=error,
                )
        else:
            self._connections[name] = FailedMCPServer(
                name=name, config=scoped, error=error,
            )
            logger.warning("MCP server '%s' connection failed: %s", name, error)

    def _on_client_closed(self, name: str, scoped: ScopedMcpServerConfig) -> None:
        """Callback when a connected client closes unexpectedly.

        Triggers auto-reconnect with exponential backoff.
        """
        conn = self._connections.get(name)
        if not isinstance(conn, ConnectedMCPServer):
            return  # Already not connected — ignore

        logger.info("MCP server '%s' connection lost, starting reconnect", name)

        # Clear tool cache for this server
        self._unregister_server_tools(name)

        # Mark as pending (reconnecting)
        current_attempt = 0
        existing_pending = self._connections.get(name)
        if isinstance(existing_pending, PendingMCPServer):
            current_attempt = existing_pending.reconnect_attempt

        self._connections[name] = PendingMCPServer(
            name=name, config=scoped,
            reconnect_attempt=current_attempt,
            max_reconnect_attempts=MAX_RECONNECT_ATTEMPTS,
        )

        # Start reconnect in background
        reconnect_task = asyncio.create_task(
            self._reconnect_server(name, scoped)
        )
        self._reconnect_tasks[name] = reconnect_task

    async def _reconnect_server(
        self,
        name: str,
        scoped: ScopedMcpServerConfig,
        *,
        is_session_expired: bool = False,
    ) -> None:
        """Reconnect a server with exponential backoff.

        Backoff: 1s → 2s → 4s → 8s → 16s (capped at 30s).
        Session expiry triggers immediate reconnect (attempt 0).
        """
        start_attempt = 0 if is_session_expired else 1

        for attempt in range(start_attempt, MAX_RECONNECT_ATTEMPTS + 1):
            if attempt > 0:
                # Calculate backoff delay
                backoff_ms = min(
                    INITIAL_BACKOFF_MS * (2 ** (attempt - 1)),
                    MAX_BACKOFF_MS,
                )
                logger.info(
                    "MCP server '%s': reconnect attempt %d/%d in %.1fs",
                    name, attempt, MAX_RECONNECT_ATTEMPTS, backoff_ms / 1000,
                )
                await asyncio.sleep(backoff_ms / 1000)

            # Update pending state
            self._connections[name] = PendingMCPServer(
                name=name, config=scoped,
                reconnect_attempt=attempt,
                max_reconnect_attempts=MAX_RECONNECT_ATTEMPTS,
            )

            try:
                # Clean up old client
                old_client = self._clients.pop(name, None)
                if old_client:
                    try:
                        await old_client.close()
                    except Exception:
                        pass

                # Attempt connection
                await self._connect_server(name, scoped)

                # Check if connected
                if isinstance(self._connections.get(name), ConnectedMCPServer):
                    logger.info(
                        "MCP server '%s': reconnected successfully (attempt %d)",
                        name, attempt,
                    )
                    self._reconnect_tasks.pop(name, None)
                    return

            except Exception as e:
                logger.warning(
                    "MCP server '%s': reconnect attempt %d failed: %s",
                    name, attempt, e,
                )

        # All attempts exhausted
        self._connections[name] = FailedMCPServer(
            name=name, config=scoped,
            error=f"Failed to reconnect after {MAX_RECONNECT_ATTEMPTS} attempts",
        )
        logger.error(
            "MCP server '%s': gave up reconnecting after %d attempts",
            name, MAX_RECONNECT_ATTEMPTS,
        )
        self._reconnect_tasks.pop(name, None)

    async def _discover_and_register_tools(self, server_name: str, client: MCPClient) -> None:
        """Discover MCP tools and register them into ToolRegistry."""
        try:
            tools = await client.list_tools()
            for tool_info in tools:
                proxy = MCPToolProxy(
                    server_name=server_name,
                    tool_name=tool_info.get("name", ""),
                    tool_info=tool_info,
                    get_client=lambda s=server_name: self._get_client(s),
                )
                self._mcp_tools[proxy.name] = proxy
                self._registry.register(proxy)
                logger.debug("Registered MCP tool: %s", proxy.name)

        except Exception as e:
            logger.warning("Failed to discover tools from '%s': %s", server_name, e)

    def _unregister_server_tools(self, server_name: str) -> None:
        """Remove all tools for a server from the registry (before reconnect)."""
        from .naming import parse_mcp_tool_name
        tools_to_remove = [
            fqn for fqn in self._mcp_tools
            if parse_mcp_tool_name(fqn) and parse_mcp_tool_name(fqn)[0] == _normalize_server_name(server_name)
        ]
        for fqn in tools_to_remove:
            if fqn in self._registry._tools:
                del self._registry._tools[fqn]
            self._registry.invalidate_schema_cache(fqn)
            del self._mcp_tools[fqn]

    async def _get_client(self, server_name: str) -> MCPClient:
        """Get a connected MCP client. Raises if not connected."""
        client = self._clients.get(server_name)
        if client and client._connected:
            return client

        raise RuntimeError(f"MCP server '{server_name}' is not connected")

    async def shutdown(self) -> None:
        """Close all MCP connections and unregister tools."""
        # Cancel reconnect tasks
        for name, task in self._reconnect_tasks.items():
            task.cancel()
        self._reconnect_tasks.clear()

        # Close all clients
        for name, client in self._clients.items():
            try:
                await client.close()
            except Exception as e:
                logger.warning("Error closing MCP server '%s': %s", name, e)

        # Remove MCP tools from registry
        for tool_name in list(self._mcp_tools.keys()):
            if tool_name in self._registry._tools:
                del self._registry._tools[tool_name]
            self._registry.invalidate_schema_cache(tool_name)

        self._clients.clear()
        self._connections.clear()
        self._mcp_tools.clear()
        self._consecutive_errors.clear()

    def get_status(self) -> dict[str, str]:
        """Get connection status of all MCP servers: {name: state_type}."""
        return {name: conn.type for name, conn in self._connections.items()}

    def get_tools(self) -> list[MCPToolProxy]:
        """Get all registered MCP tool proxies."""
        return list(self._mcp_tools.values())

    def get_connected_server_names(self) -> list[str]:
        """Get names of all successfully connected servers."""
        return [
            name for name, conn in self._connections.items()
            if isinstance(conn, ConnectedMCPServer)
        ]

    def get_failed_servers(self) -> dict[str, str]:
        """Get names and errors of failed servers: {name: error}."""
        return {
            name: conn.error
            for name, conn in self._connections.items()
            if isinstance(conn, FailedMCPServer)
        }

    def get_needs_auth_servers(self) -> list[str]:
        """Get names of servers that need authentication."""
        return [
            name for name, conn in self._connections.items()
            if isinstance(conn, NeedsAuthMCPServer)
        ]


def _normalize_server_name(name: str) -> str:
    """Normalize server name for comparison (matches naming._normalize)."""
    import re
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)
