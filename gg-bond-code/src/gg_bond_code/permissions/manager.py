"""Permission manager — mirrors permissions/ system."""

from __future__ import annotations

import fnmatch
from enum import Enum
from typing import Any

from ..config.settings import get_setting, update_setting


class PermissionDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionManager:
    """Manage tool execution permissions based on allow/deny lists."""

    def __init__(self) -> None:
        self._allowed: list[str] = list(get_setting("permissions.allow", []))
        self._denied: list[str] = list(get_setting("permissions.deny", []))
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

        # Check session grants (supports wildcard like "Bash:*")
        for grant in self._session_allowed:
            if self._match(grant, key):
                return PermissionDecision.ALLOW

        # Default: ask for dangerous tools, allow read-only
        read_only_tools = {"Read", "Glob", "Grep"}
        if tool_name in read_only_tools:
            return PermissionDecision.ALLOW

        return PermissionDecision.ASK

    def grant_session(self, tool_name: str, params: dict[str, Any], wildcard: bool = False) -> None:
        """Grant permission for the rest of this session.

        Args:
            wildcard: If True, grant all operations for this tool (e.g., "Bash:*")
                      and persist to project .settings.json.
        """
        if wildcard:
            pattern = f"{tool_name}:*"
            self._session_allowed.add(pattern)
            self._persist_allow(pattern)
        else:
            key = self._make_key(tool_name, params)
            self._session_allowed.add(key)

    def _persist_allow(self, pattern: str) -> None:
        """Persist an allow pattern to project .settings.json via public API."""
        # Read current allow list from settings
        allow_list = list(get_setting("permissions.allow", []))
        if pattern not in allow_list:
            allow_list.append(pattern)
            update_setting("permissions.allow", allow_list)
            # Keep in-memory list in sync
            self._allowed.append(pattern)

    def ask_user(self, tool_name: str, params: dict[str, Any]) -> PermissionDecision:
        """Interactively ask the user for permission. Returns ALLOW or DENY."""
        print(f"\n  ⚙ {tool_name} wants to execute:")
        for k, v in params.items():
            val = str(v)
            if len(val) > 100:
                val = val[:100] + "..."
            print(f"    {k}: {val}")
        try:
            choice = input("  Allow? [y/n/a(ll)]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return PermissionDecision.DENY

        if choice in ("a", "all"):
            self.grant_session(tool_name, params, wildcard=True)
            return PermissionDecision.ALLOW
        elif choice in ("y", "yes"):
            return PermissionDecision.ALLOW
        return PermissionDecision.DENY

    def _make_key(self, tool_name: str, params: dict[str, Any]) -> str:
        """Create a permission key from tool name and params."""
        if tool_name == "Bash":
            return f"Bash:{params.get('command', '')}"
        elif tool_name in ("Edit", "Write"):
            return f"{tool_name}:{params.get('file_path', '')}"
        return tool_name

    def _match(self, pattern: str, key: str) -> bool:
        """Simple glob-style matching."""
        return fnmatch.fnmatch(key, pattern)
