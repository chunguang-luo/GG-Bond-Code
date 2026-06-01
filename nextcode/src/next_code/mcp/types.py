"""MCP type definitions — server configs, connection states, transport types.

Uses discriminated union pattern: each state/type is a separate dataclass with
a `type` Literal field. This makes it impossible to forget handling a state —
isinstance() checks are exhaustive at runtime, and IDE autocomplete works well.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Union


# ── OAuth Configuration ────────────────────────────────────────

@dataclass
class McpOAuthConfig:
    """OAuth 2.0 configuration for remote MCP servers.

    If only resourceUrlUrl is provided, the client will discover OAuth
    endpoints via RFC 9728 (Protected Resource Metadata) and
    RFC 8414 (Authorization Server Metadata).
    """
    authorization_url: str = ""
    token_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    scopes: list[str] = field(default_factory=list)
    resource_url_url: str = ""  # RFC 9728 protected resource metadata URL


# ── Enterprise Policy ──────────────────────────────────────────

@dataclass
class McpPolicyNameMatch:
    """Match an MCP server by exact name."""
    server_name: str


@dataclass
class McpPolicyCommandMatch:
    """Match an MCP server by command (stdio only)."""
    server_command: list[str]


@dataclass
class McpPolicyUrlMatch:
    """Match an MCP server by URL pattern (wildcards supported)."""
    server_url: str


McpPolicyEntry = McpPolicyNameMatch | McpPolicyCommandMatch | McpPolicyUrlMatch


class TransportType(str, Enum):
    """MCP transport type."""
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"
    WS = "ws"
    SDK = "sdk"


class ConfigScope(str, Enum):
    """Configuration source scope — higher value = higher priority."""
    LOCAL = "local"           # .nextcode/.settings.local.json
    USER = "user"             # ~/.nextcode/.settings.json
    PROJECT = "project"       # .mcp.json
    DYNAMIC = "dynamic"       # --mcp-config CLI argument
    ENTERPRISE = "enterprise"  # managed-mcp.json (exclusive mode)


# ── Server Configurations ──────────────────────────────────────

@dataclass
class StdioServerConfig:
    """stdio transport config — local subprocess."""
    type: Literal["stdio"] = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class SSEServerConfig:
    """SSE transport config — Server-Sent Events."""
    type: Literal["sse"] = "sse"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    headers_helper: str = ""  # Script path for dynamic auth headers
    oauth: McpOAuthConfig | None = None


@dataclass
class HTTPServerConfig:
    """HTTP transport config — Streamable HTTP."""
    type: Literal["http"] = "http"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    headers_helper: str = ""  # Script path for dynamic auth headers
    oauth: McpOAuthConfig | None = None


@dataclass
class WebSocketServerConfig:
    """WebSocket transport config."""
    type: Literal["ws"] = "ws"
    url: str = ""


@dataclass
class SdkServerConfig:
    """SDK transport config — in-process SDK."""
    type: Literal["sdk"] = "sdk"
    name: str = ""


# Union: any server configuration
McpServerConfig = Union[
    StdioServerConfig, SSEServerConfig, HTTPServerConfig,
    WebSocketServerConfig, SdkServerConfig,
]


@dataclass
class ScopedMcpServerConfig:
    """Server config with scope info — tracks where the config came from."""
    config: McpServerConfig
    scope: ConfigScope
    name: str  # Server name (key in config)


# ── Connection States (Discriminated Union) ────────────────────

@dataclass
class ConnectedMCPServer:
    """Connected MCP server."""
    type: Literal["connected"] = "connected"
    name: str = ""
    config: ScopedMcpServerConfig | None = None
    server_info: dict[str, str] | None = None  # {name, version}
    capabilities: dict[str, Any] = field(default_factory=dict)


@dataclass
class FailedMCPServer:
    """Failed to connect MCP server."""
    type: Literal["failed"] = "failed"
    name: str = ""
    config: ScopedMcpServerConfig | None = None
    error: str = ""


@dataclass
class NeedsAuthMCPServer:
    """MCP server requiring authentication."""
    type: Literal["needs-auth"] = "needs-auth"
    name: str = ""
    config: ScopedMcpServerConfig | None = None


@dataclass
class PendingMCPServer:
    """MCP server pending connection."""
    type: Literal["pending"] = "pending"
    name: str = ""
    config: ScopedMcpServerConfig | None = None
    reconnect_attempt: int = 0          # Current reconnect attempt (0 = first connect)
    max_reconnect_attempts: int = 5     # Max reconnect attempts before giving up


@dataclass
class DisabledMCPServer:
    """Disabled MCP server."""
    type: Literal["disabled"] = "disabled"
    name: str = ""
    config: ScopedMcpServerConfig | None = None


# Connection state union type
MCPServerConnection = Union[
    ConnectedMCPServer, FailedMCPServer, NeedsAuthMCPServer,
    PendingMCPServer, DisabledMCPServer,
]
