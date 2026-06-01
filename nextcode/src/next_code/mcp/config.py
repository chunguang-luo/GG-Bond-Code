"""MCP configuration loading — multi-source collection, validation, dedup, merge.

Merge order (low to high priority):
  project (.mcp.json) < user (~/.nextcode/.settings.json) < local < dynamic

Enterprise exclusive mode: if managed-mcp.json exists, only enterprise servers
are returned. All other config sources are skipped.

Enterprise policy filtering:
- allowedMcpServers / deniedMcpServers control which servers are permitted.
- Denylist has absolute priority (overrides allowlist).
- Matching by name (exact), command (stdio), or URL pattern (wildcard).
- SDK servers are exempt from policy filtering.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
from pathlib import Path
from typing import Any

from .types import (
    McpServerConfig, ScopedMcpServerConfig, ConfigScope,
    McpOAuthConfig, SdkServerConfig,
    StdioServerConfig, SSEServerConfig, HTTPServerConfig,
    WebSocketServerConfig,
)

logger = logging.getLogger(__name__)


# ── Config Parsing ─────────────────────────────────────────────

def parse_server_config(name: str, raw: dict[str, Any]) -> McpServerConfig:
    """Parse a server config from raw dict, dispatching by `type` field.

    Defaults to stdio when type is absent (backward compatibility with
    configs that only specify command/args without an explicit type).
    """
    server_type = raw.get("type", "stdio")

    if server_type == "stdio" or server_type is None:
        return StdioServerConfig(
            type="stdio",
            command=raw.get("command", ""),
            args=raw.get("args", []),
            env=raw.get("env", {}),
        )
    elif server_type == "sse":
        return SSEServerConfig(
            type="sse", url=raw.get("url", ""),
            headers=raw.get("headers", {}),
            headers_helper=raw.get("headersHelper", ""),
            oauth=_parse_oauth_config(raw.get("oauth")),
        )
    elif server_type == "http":
        return HTTPServerConfig(
            type="http", url=raw.get("url", ""),
            headers=raw.get("headers", {}),
            headers_helper=raw.get("headersHelper", ""),
            oauth=_parse_oauth_config(raw.get("oauth")),
        )
    elif server_type == "ws":
        return WebSocketServerConfig(type="ws", url=raw.get("url", ""))
    elif server_type == "sdk":
        return SdkServerConfig(type="sdk", name=raw.get("name", name))
    else:
        raise ValueError(f"Unknown MCP server type: {server_type}")


# ── Signature-based Dedup ──────────────────────────────────────

def get_server_signature(config: McpServerConfig) -> str | None:
    """Generate a content signature from server config for dedup.

    Two configs pointing to the same command/URL should produce the same
    signature, even if they have different names in different config sources.
    """
    if isinstance(config, StdioServerConfig):
        if config.command:
            return f"stdio:{config.command} {' '.join(config.args)}".strip()
        return None
    elif isinstance(config, (SSEServerConfig, HTTPServerConfig, WebSocketServerConfig)):
        return f"url:{config.url}"
    elif isinstance(config, SdkServerConfig):
        return None  # SDK types have no meaningful signature
    return None


def dedup_servers(
    servers: dict[str, ScopedMcpServerConfig],
) -> dict[str, ScopedMcpServerConfig]:
    """Dedup by signature — higher priority scopes override lower ones.

    If two configs produce the same signature, the one from the higher-priority
    scope wins. This handles the case where user calls a server "slack" but a
    plugin calls it "slack-mcp" while both point to the same command.
    """
    seen_sigs: dict[str, str] = {}  # signature -> server_name
    result: dict[str, ScopedMcpServerConfig] = {}

    for name, scoped in servers.items():
        sig = get_server_signature(scoped.config)
        if sig and sig in seen_sigs:
            existing = result.get(seen_sigs[sig])
            if existing and _scope_priority(scoped.scope) > _scope_priority(existing.scope):
                # New config has higher priority — replace
                del result[seen_sigs[sig]]
                result[name] = scoped
                seen_sigs[sig] = name
            # Otherwise keep existing (higher priority already there)
        else:
            result[name] = scoped
            if sig:
                seen_sigs[sig] = name

    return result


def _scope_priority(scope: ConfigScope) -> int:
    """Return scope priority number (higher = higher priority)."""
    priorities = {
        ConfigScope.ENTERPRISE: 100,
        ConfigScope.DYNAMIC: 50,
        ConfigScope.LOCAL: 40,
        ConfigScope.PROJECT: 30,
        ConfigScope.USER: 20,
    }
    return priorities.get(scope, 0)


# ── .mcp.json Loading (project-level upward traversal) ─────────

def load_project_mcp_configs(cwd: str) -> dict[str, ScopedMcpServerConfig]:
    """Load .mcp.json files from CWD upward to root.

    Closer to CWD = higher priority (later files override earlier ones).
    """
    all_servers: dict[str, ScopedMcpServerConfig] = {}
    dirs: list[str] = []

    current = cwd
    while current != os.path.dirname(current):  # Stop at filesystem root
        dirs.append(current)
        current = os.path.dirname(current)

    # Process from root toward CWD — closer to CWD overwrites
    for dir_path in reversed(dirs):
        mcp_json_path = os.path.join(dir_path, ".mcp.json")
        if os.path.isfile(mcp_json_path):
            try:
                data = json.loads(Path(mcp_json_path).read_text())
                servers = data.get("mcpServers", {})
                for name, raw_config in servers.items():
                    config = parse_server_config(name, raw_config)
                    all_servers[name] = ScopedMcpServerConfig(
                        config=config, scope=ConfigScope.PROJECT, name=name,
                    )
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("Failed to parse %s: %s", mcp_json_path, e)

    return all_servers


# ── Enterprise Policy Filtering ────────────────────────────────

def filter_mcp_servers_by_policy(
    servers: dict[str, ScopedMcpServerConfig],
    *,
    allowed: list[dict[str, Any]] | None = None,
    denied: list[dict[str, Any]] | None = None,
) -> dict[str, ScopedMcpServerConfig]:
    """Filter MCP servers by enterprise policy.

    Rules:
    - Denylist has absolute priority — if a server matches, it's rejected.
    - If allowlist is non-empty, only servers that match are permitted.
    - If allowlist is empty, all non-denied servers are permitted.
    - SDK servers (type='sdk') are exempt from policy filtering.

    Policy entry format (dict with one of three shapes):
    - {"serverName": "exact-name"} — exact name match
    - {"serverCommand": ["npx", "mcp-server"]} — match stdio command
    - {"serverUrl": "https://*.example.com/*"} — URL wildcard match
    """
    if not allowed and not denied:
        return servers  # No policy — pass everything through

    result: dict[str, ScopedMcpServerConfig] = {}

    for name, scoped in servers.items():
        config = scoped.config

        # SDK servers are exempt from policy
        if isinstance(config, SdkServerConfig):
            result[name] = scoped
            continue

        # Check denylist first (absolute priority)
        if denied and _matches_policy(name, config, denied):
            logger.info("MCP server '%s' denied by enterprise policy", name)
            continue

        # Check allowlist
        if allowed:
            if _matches_policy(name, config, allowed):
                result[name] = scoped
            else:
                logger.info("MCP server '%s' not in enterprise allowlist", name)
        else:
            # No allowlist = everything not denied is allowed
            result[name] = scoped

    return result


def _matches_policy(
    name: str,
    config: McpServerConfig,
    policy_entries: list[dict[str, Any]],
) -> bool:
    """Check if a server matches any policy entry."""
    for entry in policy_entries:
        # Match by name
        if "serverName" in entry:
            if name == entry["serverName"]:
                return True

        # Match by command (stdio only)
        if "serverCommand" in entry and isinstance(config, StdioServerConfig):
            cmd = entry["serverCommand"]
            if isinstance(cmd, list) and cmd:
                # Match the command + args
                full_cmd = [config.command] + config.args
                if full_cmd == cmd or config.command == cmd[0]:
                    return True

        # Match by URL pattern (remote only)
        if "serverUrl" in entry and isinstance(config, (SSEServerConfig, HTTPServerConfig, WebSocketServerConfig)):
            pattern = entry["serverUrl"]
            if fnmatch.fnmatch(config.url, pattern):
                return True

    return False


# ── Enterprise Exclusive Mode ──────────────────────────────────

_MANAGED_MCP_PATHS: list[Path] = [
    # macOS: /Library/Application Support/NextCode/managed-mcp.json
    Path("/Library/Application Support/NextCode/managed-mcp.json"),
    # Linux: /etc/nextcode/managed-mcp.json
    Path("/etc/nextcode/managed-mcp.json"),
    # Windows: C:\ProgramData\NextCode\managed-mcp.json (handled on Windows)
]


def _has_enterprise_config() -> bool:
    """Check if an enterprise-managed MCP config exists."""
    return any(p.exists() for p in _MANAGED_MCP_PATHS)


def _load_enterprise_configs() -> dict[str, ScopedMcpServerConfig]:
    """Load enterprise-managed MCP server configs."""
    for path in _MANAGED_MCP_PATHS:
        if path.exists():
            try:
                data = json.loads(path.read_text())
                servers = data.get("mcpServers", {})
                return _extract_mcp_servers(servers, ConfigScope.ENTERPRISE)
            except (json.JSONDecodeError, ValueError) as e:
                logger.error("Failed to parse enterprise MCP config %s: %s", path, e)
    return {}


def _load_enterprise_policy() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load enterprise policy (allowedMcpServers, deniedMcpServers).

    Returns (allowed, denied) policy entry lists.
    """
    for path in _MANAGED_MCP_PATHS:
        if path.exists():
            try:
                data = json.loads(path.read_text())
                allowed = data.get("allowedMcpServers", [])
                denied = data.get("deniedMcpServers", [])
                return allowed, denied
            except (json.JSONDecodeError, ValueError) as e:
                logger.error("Failed to parse enterprise policy %s: %s", path, e)
    return [], []


# ── Main Entry: Collect All MCP Configs ────────────────────────

def get_all_mcp_configs(
    dynamic_configs: dict[str, Any] | None = None,
) -> dict[str, ScopedMcpServerConfig]:
    """Collect and merge MCP server configs from all sources.

    Merge order (priority low → high):
      project < user < local < dynamic

    Enterprise exclusive mode: if managed-mcp.json exists, only return
    enterprise servers (all other sources are skipped).

    Enterprise policy: apply allowlist/denylist after merging.
    """
    # Enterprise exclusive mode
    if _has_enterprise_config():
        enterprise_servers = _load_enterprise_configs()
        if enterprise_servers:
            logger.info("Enterprise MCP config detected — using exclusive mode")
            # Still apply enterprise policy to enterprise servers
            allowed, denied = _load_enterprise_policy()
            return filter_mcp_servers_by_policy(
                enterprise_servers, allowed=allowed, denied=denied,
            )

    all_servers: dict[str, ScopedMcpServerConfig] = {}

    # 1. Project configs (.mcp.json upward traversal)
    cwd = os.getcwd()
    project_servers = load_project_mcp_configs(cwd)
    all_servers.update(project_servers)

    # 2. User configs (~/.nextcode/.settings.json mcpServers)
    user_servers = _load_user_mcp_configs()
    for name, scoped in user_servers.items():
        all_servers[name] = scoped  # user overrides project

    # 3. Local configs (.nextcode/.settings.local.json mcpServers)
    local_servers = _load_local_mcp_configs()
    for name, scoped in local_servers.items():
        all_servers[name] = scoped  # local overrides user

    # 4. Dynamic configs (--mcp-config CLI argument)
    if dynamic_configs:
        for name, raw_config in dynamic_configs.items():
            config = parse_server_config(name, raw_config)
            all_servers[name] = ScopedMcpServerConfig(
                config=config, scope=ConfigScope.DYNAMIC, name=name,
            )

    # Signature dedup
    all_servers = dedup_servers(all_servers)

    # Enterprise policy filtering (non-exclusive mode)
    if _has_enterprise_config():
        allowed, denied = _load_enterprise_policy()
        if allowed or denied:
            all_servers = filter_mcp_servers_by_policy(
                all_servers, allowed=allowed, denied=denied,
            )

    return all_servers


# ── Internal Helpers ───────────────────────────────────────────

def _load_user_mcp_configs() -> dict[str, ScopedMcpServerConfig]:
    """Load mcpServers from ~/.nextcode/.settings.json."""
    path = Path.home() / ".nextcode" / ".settings.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return _extract_mcp_servers(data.get("mcpServers", {}), ConfigScope.USER)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to load user MCP configs: %s", e)
        return {}


def _load_local_mcp_configs() -> dict[str, ScopedMcpServerConfig]:
    """Load mcpServers from .nextcode/.settings.local.json."""
    local_path = Path.cwd() / ".nextcode" / ".settings.local.json"
    if not local_path.exists():
        return {}
    try:
        data = json.loads(local_path.read_text())
        return _extract_mcp_servers(data.get("mcpServers", {}), ConfigScope.LOCAL)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to load local MCP configs: %s", e)
        return {}


def _extract_mcp_servers(
    raw_servers: dict[str, Any], scope: ConfigScope,
) -> dict[str, ScopedMcpServerConfig]:
    """Extract MCP server configs from a raw dict."""
    result: dict[str, ScopedMcpServerConfig] = {}
    for name, raw_config in raw_servers.items():
        try:
            config = parse_server_config(name, raw_config)
            result[name] = ScopedMcpServerConfig(
                config=config, scope=scope, name=name,
            )
        except ValueError:
            logger.warning("Skipping invalid MCP server config: %s", name)
    return result


def _parse_oauth_config(raw: dict[str, Any] | None) -> McpOAuthConfig | None:
    """Parse OAuth config from raw dict."""
    if not raw:
        return None
    return McpOAuthConfig(
        authorization_url=raw.get("authorizationUrl", ""),
        token_url=raw.get("tokenUrl", ""),
        client_id=raw.get("clientId", ""),
        client_secret=raw.get("clientSecret", ""),
        scopes=raw.get("scopes", []),
        resource_url_url=raw.get("resourceUrlUrl", ""),
    )
