"""Permission manager — mirrors permissions/ system.

Enhanced with:
- Permission modes (default/plan/acceptEdits/bypassPermissions/dontAsk)
- Shell content-level rule matching (exact/prefix/wildcard)
- Smart rule suggestions via get_command_prefix()
- Safe env var stripping for Bash permission keys
- ask rules support (always prompt, even for read-only tools)
- Decision pipeline: deny → ask → tool internal → safety → mode → allow → default ask
"""

from __future__ import annotations

import fnmatch
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..config.settings import get_setting, update_setting
from .modes import PermissionMode, is_write_tool
from .path_validation import validate_path, is_path_allowed, check_path_safety_for_auto_edit, expand_tilde
from .denial_tracking import DenialTrackingState, record_denial, record_success, should_fallback_to_prompting

if TYPE_CHECKING:
    from ..tools.base import ToolRegistry


class PermissionDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionManager:
    """Manage tool execution permissions based on allow/deny/ask lists and mode."""

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
        # ── Mode support ──────────────────────────────────────────
        self._mode: PermissionMode | None = (
            PermissionMode(mode) if mode else None
        )
        self._avoid_permission_prompts: bool = avoid_permission_prompts
        # Working directory for acceptEdits mode path checks
        self._cwd: str = str(Path.cwd())
        # ── Denial tracking ──────────────────────────────────────────
        self._denial_state: DenialTrackingState = DenialTrackingState()
        self._is_headless: bool = False

    # ── Mode property ──────────────────────────────────────────────────

    @property
    def mode(self) -> PermissionMode | None:
        """Current permission mode."""
        return self._mode

    @mode.setter
    def mode(self, value: PermissionMode | str | None) -> None:
        """Set permission mode."""
        if isinstance(value, str):
            self._mode = PermissionMode(value)
        else:
            self._mode = value

    @property
    def cwd(self) -> str:
        """Working directory for path checks."""
        return self._cwd

    @cwd.setter
    def cwd(self, value: str) -> None:
        """Set working directory."""
        self._cwd = value

    @property
    def is_headless(self) -> bool:
        """Whether this manager is running in headless mode (no user interaction)."""
        return self._is_headless

    @is_headless.setter
    def is_headless(self, value: bool) -> None:
        """Set headless mode."""
        self._is_headless = value

    @property
    def denial_state(self) -> DenialTrackingState:
        """Current denial tracking state."""
        return self._denial_state

    def reset_denial_state(self) -> None:
        """Reset denial tracking counters."""
        self._denial_state = DenialTrackingState()

    # ── Main check pipeline ────────────────────────────────────────────

    def _get_operation_type(self, tool_name: str, params: dict[str, Any]) -> str | None:
        """Determine the operation type for a tool."""
        if tool_name in ("Edit", "Write"):
            return "write"
        if tool_name == "Read":
            return "read"
        if tool_name in ("Glob", "Grep"):
            return "read"
        if tool_name == "Bash":
            # Bash can have both read and write operations
            command = params.get("command", "").lower()
            if any(cmd in command for cmd in ["rm", "rmdir", "delete", "unlink", "rm -rf"]):
                return "delete"
            if any(cmd in command for cmd in ["mv", "cp", "tee", ">", ">>"]):
                return "write"
            return "read"
        return None

    def _extract_paths_from_tool(self, tool_name: str, params: dict[str, Any]) -> list[tuple[str, str]]:
        """Extract (raw_path, operation_type) tuples from tool params.

        Returns list of (path, operation) for path validation.
        """
        paths = []

        if tool_name in ("Edit", "Write", "Read"):
            path = params.get("file_path", "")
            if path:
                op = "write" if tool_name in ("Edit", "Write") else "read"
                paths.append((path, op))

        elif tool_name == "Glob":
            path = params.get("path", ".")
            paths.append((path, "read"))

        elif tool_name == "Grep":
            path = params.get("path", ".")
            paths.append((path, "read"))

        elif tool_name == "Bash":
            # Extract file paths from bash command for validation
            command = params.get("command", "")
            extracted = self._extract_paths_from_command(command)
            paths.extend(extracted)

        return paths

    def _extract_paths_from_command(self, command: str) -> list[tuple[str, str]]:
        """Extract potential file paths from a bash command.

        This is a simplified extraction - looks for common file path patterns.
        """
        paths = []
        command_lower = command.lower()

        # Detect operation type from command
        if any(cmd in command_lower for cmd in ["rm ", "rmdir ", "unlink ", "delete "]):
            op = "delete"
        elif any(cmd in command_lower for cmd in ["mv ", "cp ", "tee ", " >", " >>", "wget ", "curl "]):
            op = "write"
        else:
            op = "read"

        # Extract paths matching file patterns
        # Look for paths starting with / or ~
        path_pattern = re.compile(r'(?:^|[;\s])([/~][^\s;<>|"\'\\]+)')
        for match in path_pattern.finditer(command):
            path = match.group().strip()
            # Filter to likely file paths
            if path.startswith("/") or path.startswith("~"):
                if not any(path.startswith(x) for x in ["/usr", "/bin", "/lib", "/proc", "/sys"]):
                    paths.append((path, op))
                elif any(safe in path for safe in ["/dev/null", "/dev/zero"]):
                    continue  # Skip /dev/null and similar
                else:
                    paths.append((path, op))

        return paths

    def _check_path_validation(self, tool_name: str, params: dict[str, Any]) -> tuple[bool, str]:
        """Validate paths in tool params using validate_path().

        Returns (valid, reason) - (True, "") if valid, (False, reason) if blocked.
        """
        path_tuples = self._extract_paths_from_tool(tool_name, params)
        for raw_path, operation in path_tuples:
            valid, reason = validate_path(raw_path, self._cwd, operation)
            if not valid:
                return False, reason
        return True, ""

    def _resolve_path(self, raw_path: str) -> str:
        """Resolve a raw path: expand ~ and resolve relative."""
        expanded = expand_tilde(raw_path)
        return str(Path(expanded).resolve())

    def check(
        self, tool_name: str, params: dict[str, Any], *, registry: ToolRegistry | None = None,
    ) -> PermissionDecision:
        """Check if a tool execution is permitted.

        Wraps _check_inner() with denial tracking. When the circuit breaker
        triggers (3 consecutive or 20 total denials):
        - Interactive mode: falls back to user prompting (return ASK)
        - Headless mode: aborts (return DENY)

        Decision pipeline (mirrors hasPermissionsToUseToolInner):
        0.  Path validation (validate_path) — TOCTOU prevention
        1a. Whole-tool deny rules (highest priority)
        1b. Whole-tool ask rules
        1c. Content-level deny rules (e.g. Bash(rm -rf:*))
        1d. Content-level ask rules (e.g. Bash(npm publish:*))
        2a. bypassPermissions mode → allow
        2b. Plan mode → deny write tools
        2c. AcceptEdits mode → auto-allow working-dir edits
        2d. Whole-tool allow rules
        2e. Content-level allow rules
        3.  Session grants
        4.  Auto-allow read-only tools
        5.  dontAsk mode → auto-approve
        6.  avoid_permission_prompts → auto-deny
        7.  Default → ASK

        Args:
            tool_name: Name of the tool to check.
            params: Tool parameters.
            registry: Optional ToolRegistry to look up is_read_only() on the tool.
        """
        result = self._check_inner(tool_name, params, registry=registry)

        # Track denials for circuit breaker
        if result == PermissionDecision.DENY:
            self._denial_state = record_denial(self._denial_state)
            # Check circuit breaker
            if should_fallback_to_prompting(self._denial_state):
                if self._is_headless:
                    # Headless: can't prompt user, must abort
                    return PermissionDecision.DENY
                # Interactive: fall back to user prompting
                return PermissionDecision.ASK
        elif result == PermissionDecision.ALLOW:
            self._denial_state = record_success(self._denial_state)
        elif result == PermissionDecision.ASK and self._is_headless:
            # Headless agents can't prompt the user.
            # Try permission hooks first, then fall back to deny.
            result = self._try_headless_hooks(tool_name, params)
            if result is not None:
                return result
            return PermissionDecision.DENY

        return result

    def _try_headless_hooks(
        self, tool_name: str, params: dict[str, Any],
    ) -> PermissionDecision | None:
        """Try registered permission hooks for headless agent decisions.

        Returns None if no hook provides a decision (meaning "fall through
        to default headless behavior, which is DENY").

        Uses run_coroutine_threadsafe or similar to call async hooks from
        sync context. If no event loop is running, hooks are skipped.
        """
        import asyncio
        from .headless_hooks import run_permission_hooks, get_hook_count

        if get_hook_count() == 0:
            return None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop — hooks can't be called synchronously
            return None

        # We're inside an async context — call hooks directly via
        # the hook runner. Since check() is sync but called from async
        # QueryRunner methods, we create a task and wait.
        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(
            run_permission_hooks(tool_name, params),
            loop,
        )
        try:
            return future.result(timeout=5.0)
        except concurrent.futures.TimeoutError:
            import logging
            logging.getLogger(__name__).warning(
                "Permission hooks timed out for tool=%s", tool_name,
            )
            return None

    def _check_inner(
        self, tool_name: str, params: dict[str, Any], *, registry: ToolRegistry | None = None,
    ) -> PermissionDecision:
        """Inner permission check pipeline (without denial tracking wrapper)."""
        # === Phase 0: Path validation (TOCTOU prevention) ===

        valid, reason = self._check_path_validation(tool_name, params)
        if not valid:
            return PermissionDecision.DENY

        key = self._make_key(tool_name, params)

        # === Phase 1: Deny/Ask rules (always checked) ===

        # 1a. Whole-tool deny rules
        if self._matches_whole_tool(tool_name, self._denied):
            return PermissionDecision.DENY

        # 1b. Whole-tool ask rules
        if self._matches_whole_tool(tool_name, self._ask_rules):
            return PermissionDecision.ASK

        # 1c. Content-level deny rules (e.g. Bash(rm -rf:*))
        if self._matches_content_rule(tool_name, params, self._denied):
            return PermissionDecision.DENY

        # 1d. Content-level ask rules (e.g. Bash(npm publish:*))
        # These are NOT skipped in bypass mode — user explicitly set them
        if self._matches_content_rule(tool_name, params, self._ask_rules):
            return PermissionDecision.ASK

        # === Phase 2: Mode-based checks ===

        # 2a. bypassPermissions mode — skip remaining checks
        if self._mode == PermissionMode.BYPASS_PERMISSIONS:
            return PermissionDecision.ALLOW

        # 2b. Plan mode — only allow read-only tools
        if self._mode == PermissionMode.PLAN:
            if registry is not None:
                tool = registry.get(tool_name)
                if tool is not None and not tool.is_read_only(params):
                    return PermissionDecision.ASK
            elif is_write_tool(tool_name):
                return PermissionDecision.ASK

        # 2c. AcceptEdits mode — use is_path_allowed for working directory checks
        if self._mode == PermissionMode.ACCEPT_EDITS:
            op_type = self._get_operation_type(tool_name, params)
            if op_type in ("write", "read"):
                path_tuples = self._extract_paths_from_tool(tool_name, params)
                for raw_path, operation in path_tuples:
                    resolved = self._resolve_path(raw_path)
                    allowed, reason = is_path_allowed(
                        resolved_path=resolved,
                        cwd=self._cwd,
                        operation_type=operation,
                        allow_rules=self._allowed,
                        deny_rules=self._denied,
                        permission_mode="acceptEdits",
                    )
                    if allowed:
                        return PermissionDecision.ALLOW

        # === Phase 3: Allow rules ===

        # 3a. Whole-tool allow rules
        if self._matches_whole_tool(tool_name, self._allowed):
            return PermissionDecision.ALLOW

        # 3b. Content-level allow rules
        if self._matches_content_rule(tool_name, params, self._allowed):
            return PermissionDecision.ALLOW

        # === Phase 4: Session grants ===

        for grant in self._session_allowed:
            if self._match(key, grant):
                return PermissionDecision.ALLOW

        # === Phase 5: Read-only auto-allow ===

        if registry is not None:
            tool = registry.get(tool_name)
            if tool is not None and tool.is_read_only(params):
                return PermissionDecision.ALLOW

        # === Phase 6: dontAsk mode ===
        if self._mode == PermissionMode.DONT_ASK:
            return PermissionDecision.ALLOW

        # === Phase 7: Non-interactive sub-agents ===

        if self._avoid_permission_prompts:
            return PermissionDecision.DENY

        # === Phase 8: Default → ASK ===
        return PermissionDecision.ASK

    # ── Rule matching helpers ──────────────────────────────────────────

    def _matches_whole_tool(self, tool_name: str, rules: list[str]) -> bool:
        """Check if any rule matches the whole tool (no content spec).

        A rule matches the whole tool if it's just the tool name,
        or ToolName(*), or ToolName().
        """
        for rule_str in rules:
            from .shell_rules import parse_rule_string
            rule_tool, rule_content = parse_rule_string(rule_str)
            if rule_tool == tool_name and rule_content is None:
                return True
        return False

    def _matches_content_rule(
        self, tool_name: str, params: dict[str, Any], rules: list[str],
    ) -> bool:
        """Check if any rule matches the tool with content-level matching.

        For Bash rules, uses shell_rules.match_shell_rule() which supports
        exact, prefix, and wildcard matching.

        For Edit/Write rules, uses path glob matching.
        """
        for rule_str in rules:
            from .shell_rules import parse_rule_string
            rule_tool, rule_content = parse_rule_string(rule_str)
            if rule_tool != tool_name:
                continue
            if rule_content is None:
                continue  # Whole-tool rule, handled by _matches_whole_tool

            # Bash: use shell rule matching
            if tool_name == "Bash":
                command = params.get("command", "")
                # Normalize command for matching (strip safe env vars and compound prefix)
                from ..tools.bash_rule_suggestion import strip_safe_env_vars, strip_compound_prefix
                normalized = strip_safe_env_vars(command)
                normalized = strip_compound_prefix(normalized)
                from .shell_rules import match_shell_rule
                if match_shell_rule(rule_content, normalized):
                    return True

            # Edit/Write: path glob matching
            elif tool_name in ("Edit", "Write"):
                file_path = params.get("file_path", "")
                if self._path_matches_content(rule_content, file_path):
                    return True

            # Other tools: simple string match on first param
            else:
                for v in params.values():
                    if isinstance(v, str) and fnmatch.fnmatch(v, rule_content):
                        return True

        return False

    def _path_matches_content(self, rule_content: str, file_path: str) -> bool:
        """Match a file path against a rule content pattern."""
        # Try direct glob match
        if fnmatch.fnmatch(file_path, rule_content):
            return True
        # Try matching against just the filename
        if fnmatch.fnmatch(os.path.basename(file_path), rule_content):
            return True
        # Try matching against the path relative to cwd
        try:
            rel = os.path.relpath(file_path, self._cwd)
            if fnmatch.fnmatch(rel, rule_content):
                return True
        except ValueError:
            pass
        return False

    # ── Session grants ─────────────────────────────────────────────────

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
                    pattern = f"Bash({suggested_prefix}:*)"
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

        Refuses to persist overly broad patterns like Bash:* or Agent:*
        that would effectively bypass the permission system.
        """
        import logging
        _logger = logging.getLogger(__name__)

        # Guard: never persist overly broad patterns for Bash and Agent
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

    # ── Key generation ─────────────────────────────────────────────────

    def _make_key(self, tool_name: str, params: dict[str, Any]) -> str:
        """Create a permission key from tool name and params.

        For Bash commands, normalizes the command so that compound commands
        like `cd /dir && source venv && pytest ...` match rules like
        `Bash(pytest:*)` instead of requiring `Bash(cd:*)` rules.

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

    def set_session_allowed_tools(self, tools: list[str]) -> None:
        """Replace session-level allowed tools for a sub-agent.

        Used by create_subagent_context() to enforce AgentDefinition.tools.
        Replaces the existing session grants (not the persistent allow list).
        """
        self._session_allowed = set(tools)
