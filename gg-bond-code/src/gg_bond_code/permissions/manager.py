"""Permission manager — mirrors permissions/ system."""

from __future__ import annotations

from enum import Enum
from typing import Any

from gg_bond_code.config.settings import get_setting


class PermissionDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionManager:
    """Manage tool execution permissions based on allow/deny lists."""

    def __init__(self) -> None:
        self._allowed: list[str] = get_setting("permissions.allow", [])
        self._denied: list[str] = get_setting("permissions.deny", [])
        # Runtime session grants (user approved during this session)
        self._session_allowed: set[str] = set()

    def check(self, tool_name: str, params: dict[str, Any]) -> PermissionDecision:
        """Check if a tool execution is permitted."""
        key = self._make_key(tool_name, params)

        # Check deny list first (highest priority)
        for pattern in self._denied:
            if self._match(pattern, key):
                return PermissionDecision.DENY

        # Check allow list
        for pattern in self._allowed:
            if self._match(pattern, key):
                return PermissionDecision.ALLOW

        # Check session grants
        if key in self._session_allowed:
            return PermissionDecision.ALLOW

        # Default: ask for dangerous tools, allow read-only
        read_only_tools = {"Read", "Glob", "Grep"}
        if tool_name in read_only_tools:
            return PermissionDecision.ALLOW

        return PermissionDecision.ASK

    def grant_session(self, tool_name: str, params: dict[str, Any]) -> None:
        """Grant permission for the rest of this session."""
        key = self._make_key(tool_name, params)
        self._session_allowed.add(key)

    def _make_key(self, tool_name: str, params: dict[str, Any]) -> str:
        """Create a permission key from tool name and params."""
        if tool_name == "Bash":
            return f"Bash:{params.get('command', '')}"
        elif tool_name in ("Edit", "Write"):
            return f"{tool_name}:{params.get('file_path', '')}"
        return tool_name

    def _match(self, pattern: str, key: str) -> bool:
        """Simple glob-style matching."""
        import fnmatch
        return fnmatch.fnmatch(key, pattern)
