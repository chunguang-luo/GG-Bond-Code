"""Permission manager — mirrors permissions/ system.

Enhanced with:
- Smart rule suggestions via get_command_prefix()
- Safe env var stripping for Bash permission keys
- ask rules support (always prompt, even for read-only tools)
"""

from __future__ import annotations

import fnmatch
from enum import Enum
from typing import Any, TYPE_CHECKING

from ..config.settings import get_setting, update_setting

if TYPE_CHECKING:
    from ..tools.base import ToolRegistry


class PermissionDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionManager:
    """Manage tool execution permissions based on allow/deny/ask lists."""

    def __init__(self) -> None:
        self._allowed: list[str] = list(get_setting("permissions.allow", []))
        self._denied: list[str] = list(get_setting("permissions.deny", []))
        self._ask_rules: list[str] = list(get_setting("permissions.ask", []))
        # Runtime session grants (user approved during this session)
        self._session_allowed: set[str] = set()

    def check(
        self, tool_name: str, params: dict[str, Any], *, registry: ToolRegistry | None = None,
    ) -> PermissionDecision:
        """Check if a tool execution is permitted.

        Args:
            tool_name: Name of the tool to check.
            params: Tool parameters.
            registry: Optional ToolRegistry to look up is_read_only() on the tool.
        """
        key = self._make_key(tool_name, params)

        # Check deny list first (highest priority)
        for pattern in self._denied:
            if self._match(pattern, key):
                return PermissionDecision.DENY

        # Check ask rules (always prompt, even for read-only tools)
        for pattern in self._ask_rules:
            if self._match(pattern, key):
                return PermissionDecision.ASK

        # Check allow list
        for pattern in self._allowed:
            if self._match(pattern, key):
                return PermissionDecision.ALLOW

        # Check session grants (supports wildcard like "Bash:*")
        for grant in self._session_allowed:
            if self._match(grant, key):
                return PermissionDecision.ALLOW

        # Auto-allow read-only tools via is_read_only() on the tool itself
        if registry is not None:
            tool = registry.get(tool_name)
            if tool is not None and tool.is_read_only(params):
                return PermissionDecision.ALLOW

        return PermissionDecision.ASK

    def grant_session(self, tool_name: str, params: dict[str, Any], wildcard: bool = False) -> str | None:
        """Grant permission for the rest of this session.

        Args:
            wildcard: If True, grant all operations for this tool (e.g., "Bash:*")
                      and persist to project .settings.json.
            params: Tool parameters (used for specific grants and prefix suggestions).

        Returns:
            The suggested prefix rule if applicable, or None.
        """
        import logging
        logger = logging.getLogger(__name__)
        logger.info("grant_session: tool=%s, wildcard=%s, params=%s", tool_name, wildcard, params)

        suggested_prefix = None

        if wildcard:
            # For Bash commands, try to suggest a more specific prefix
            if tool_name == "Bash" and "command" in params:
                from ..tools.bash_rule_suggestion import get_command_prefix
                suggested_prefix = get_command_prefix(params["command"])

                if suggested_prefix:
                    # Use the prefix as the grant pattern instead of Bash:*
                    pattern = f"Bash:{suggested_prefix}:*"
                else:
                    pattern = f"{tool_name}:*"
            else:
                pattern = f"{tool_name}:*"

            self._session_allowed.add(pattern)
            self._persist_allow(pattern)
        else:
            key = self._make_key(tool_name, params)
            self._session_allowed.add(key)

        return suggested_prefix

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
            suggested = self.grant_session(tool_name, params, wildcard=True)
            if suggested and tool_name == "Bash":
                print(f"  Rule added: Bash({suggested}:*)")
            return PermissionDecision.ALLOW
        elif choice in ("y", "yes"):
            return PermissionDecision.ALLOW
        return PermissionDecision.DENY

    def _make_key(self, tool_name: str, params: dict[str, Any]) -> str:
        """Create a permission key from tool name and params.

        For Bash commands, strips safe environment variables from
        the command so that `NODE_ENV=test npm run build` matches
        the rule for `npm run` rather than requiring a separate rule.
        """
        if tool_name == "Bash":
            command = params.get("command", "")
            # Strip safe env vars for matching
            from ..tools.bash_rule_suggestion import strip_safe_env_vars
            stripped = strip_safe_env_vars(command)
            return f"Bash:{stripped}"
        elif tool_name in ("Edit", "Write"):
            return f"{tool_name}:{params.get('file_path', '')}"
        return tool_name

    def _match(self, pattern: str, key: str) -> bool:
        """Simple glob-style matching."""
        return fnmatch.fnmatch(key, pattern)
