"""MCP tool naming convention — mcp__<server>__<tool> format.

Why this format:
- The `mcp__` prefix is recognized by agent_filter.py to bypass tool filtering.
  MCP tools are trusted because the user explicitly connected that server.
- The double-underscore separator avoids ambiguity: server and tool names
  can themselves contain single underscores without collisions.
- Name normalization replaces non-alphanumeric chars with underscores to
  satisfy API constraints on tool names.
"""
from __future__ import annotations

import re

MCP_PREFIX = "mcp__"


def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Build the fully-qualified MCP tool name: mcp__<server>__<tool>."""
    return f"{MCP_PREFIX}{_normalize(server_name)}__{_normalize(tool_name)}"


def parse_mcp_tool_name(fqn: str) -> tuple[str, str] | None:
    """Parse a fully-qualified name into (server_name, tool_name).

    Returns None if the name doesn't follow the mcp__ convention.
    """
    if not fqn.startswith(MCP_PREFIX):
        return None
    rest = fqn[len(MCP_PREFIX):]
    parts = rest.split("__", 1)
    if len(parts) != 2:
        return None
    return (parts[0], parts[1])


def is_mcp_tool_name(name: str) -> bool:
    """Check if a name follows the MCP tool naming convention."""
    return name.startswith(MCP_PREFIX)


def _normalize(name: str) -> str:
    """Normalize a name: replace non-alphanumeric chars with underscores."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)
