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

    def __init__(
        self,
        *,
        mode: str | None = None,
        avoid_permission_prompts: bool = False,
    ) -> None:
        self._allowed: list[str] = list(get_setting("permissions.allow", []))
        self._denied: list[str] = list(get_setting("permissions.deny", []))
        self._ask_rules: list[str] = list(get_setting("permissions.ask", []))
        # Runtime session grants (user approved during this session)
        self._session_allowed: set[str] = set()
        # ── Agent support ──────────────────────────────────────────
        self._mode: str | None = mode
        self._avoid_permission_prompts: bool = avoid_permission_prompts

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

        # dontAsk mode: auto-approve anything not explicitly denied
        # Used by /dangerous-bg-no-ask to let background agents run freely
        if self._mode == "dontAsk":
            return PermissionDecision.ALLOW

        # Sub-agents that cannot interact with the user should auto-deny
        # instead of asking — otherwise the entire flow blocks on an
        # unanswered permission prompt.
        if self._avoid_permission_prompts:
            return PermissionDecision.DENY

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
                    pattern = f"Bash:{suggested_prefix}*"
                else:
                    # Cannot extract a specific prefix — only allow for this session,
                    # do NOT persist Bash:* to settings (too broad)
                    logger.warning(
                        "Cannot extract specific prefix for Bash command, "
                        "session-only grant (not persisted): %s",
                        params["command"][:100],
                    )
                    key = self._make_key(tool_name, params)
                    self._session_allowed.add(key)
                    return None
            elif tool_name in ("Edit", "Write"):
                # For file operations, allow all files (Edit:* / Write:*)
                # Users choosing "always allow" for Edit/Write typically want full access
                pattern = f"{tool_name}:*"
            else:
                # Other tools — don't persist broad Tool:* rules
                logger.warning(
                    "Broad %s:* rule not persisted, session-only grant", tool_name,
                )
                key = self._make_key(tool_name, params)
                self._session_allowed.add(key)
                return None

            self._session_allowed.add(pattern)
            self._persist_allow(pattern)
        else:
            key = self._make_key(tool_name, params)
            self._session_allowed.add(key)

        return suggested_prefix

    def _persist_allow(self, pattern: str) -> None:
        """Persist an allow pattern to project .settings.json via public API.

        Refuses to persist overly broad patterns like Bash:* or Edit:*
        that would effectively bypass the permission system.
        """
        import logging
        _logger = logging.getLogger(__name__)

        # Guard: never persist overly broad patterns for Bash and Agent
        # (Edit:* and Write:* are allowed — users may want full file access)
        if pattern in ("Bash:*", "Agent:*"):
            _logger.warning("Refusing to persist overly broad pattern: %s", pattern)
            return

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

        For Bash commands, normalizes the command so that compound commands
        like `cd /dir && source venv && pytest ...` match rules like
        `Bash:pytest:*` instead of requiring `Bash:cd:*` rules.

        Strips:
        - Safe environment variable assignments (NODE_ENV=test ...)
        - Compound command prefixes (cd ..., source ...) that are just setup
        """
        if tool_name == "Bash":
            command = params.get("command", "")
            # Strip safe env vars and compound command prefixes
            from ..tools.bash_rule_suggestion import strip_safe_env_vars, strip_compound_prefix
            stripped = strip_safe_env_vars(command)
            stripped = strip_compound_prefix(stripped)
            return f"Bash:{stripped}"
        elif tool_name in ("Edit", "Write"):
            return f"{tool_name}:{params.get('file_path', '')}"
        return tool_name

    def _match(self, pattern: str, key: str) -> bool:
        """Match a permission key against a pattern.

        Unlike filesystem glob, `*` matches any character including `/`,
        since Bash command keys contain paths and arguments with slashes.
        """
        # Convert glob pattern to regex where * matches everything
        import re as _re
        regex = ""
        for ch in pattern:
            if ch == "*":
                regex += ".*"
            elif ch == "?":
                regex += "."
            else:
                regex += _re.escape(ch)
        return bool(_re.fullmatch(regex, key))

    # ── Agent support ──────────────────────────────────────────────────

    @property
    def mode(self) -> str | None:
        """Permission mode — e.g. 'default', 'plan'. None = not set."""
        return self._mode

    def set_session_allowed_tools(self, tools: list[str]) -> None:
        """Replace session-level allowed tools for a sub-agent.

        Used by create_subagent_context() to enforce AgentDefinition.tools.
        Replaces the existing session grants (not the persistent allow list).
        """
        self._session_allowed = set(tools)
