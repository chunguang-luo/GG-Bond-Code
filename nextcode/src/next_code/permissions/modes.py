"""Permission modes — mirrors Claude Code's permission mode system.

Defines the 5 external permission modes (default, plan, acceptEdits,
bypassPermissions, dontAsk) and their behaviors.

Mode switching order (external users):
    default → acceptEdits → plan → default
"""

from __future__ import annotations

from enum import Enum


class PermissionMode(str, Enum):
    """Permission modes representing the user's global trust level.

    Mirrors Claude Code's EXTERNAL_PERMISSION_MODES + 'dontAsk'.
    """

    DEFAULT = "default"
    """Lowest trust — every non-read-only operation requires user confirmation."""

    PLAN = "plan"
    """Read-only — only reading/searching is allowed, writes require confirmation."""

    ACCEPT_EDITS = "acceptEdits"
    """Medium trust — file edits within the working directory are auto-allowed,
    other operations still require confirmation."""

    BYPASS_PERMISSIONS = "bypassPermissions"
    """High trust — skip most permission checks (but safety checks still apply)."""

    DONT_ASK = "dontAsk"
    """Special — don't pop confirmation dialogs; auto-deny operations that need
    confirmation. Used by background agents and /dangerous-bg-no-ask."""


# Ordered cycle for external users: default → acceptEdits → plan → default
_EXTERNAL_MODE_CYCLE: tuple[PermissionMode, ...] = (
    PermissionMode.DEFAULT,
    PermissionMode.ACCEPT_EDITS,
    PermissionMode.PLAN,
)

# Modes that include bypassPermissions in the cycle (when available)
_EXTERNAL_MODE_CYCLE_WITH_BYPASS: tuple[PermissionMode, ...] = (
    PermissionMode.DEFAULT,
    PermissionMode.ACCEPT_EDITS,
    PermissionMode.PLAN,
    PermissionMode.BYPASS_PERMISSIONS,
)


def get_next_permission_mode(
    current: PermissionMode,
    *,
    bypass_available: bool = False,
) -> PermissionMode:
    """Get the next permission mode in the cycle.

    Args:
        current: The current permission mode.
        bypass_available: Whether bypassPermissions mode is available.

    Returns:
        The next mode in the cycle.
    """
    if current == PermissionMode.DONT_ASK:
        return PermissionMode.DEFAULT

    if current == PermissionMode.BYPASS_PERMISSIONS:
        return PermissionMode.DEFAULT

    cycle = _EXTERNAL_MODE_CYCLE_WITH_BYPASS if bypass_available else _EXTERNAL_MODE_CYCLE

    try:
        idx = cycle.index(current)
        next_idx = (idx + 1) % len(cycle)
        return cycle[next_idx]
    except ValueError:
        return PermissionMode.DEFAULT


def is_write_tool(tool_name: str) -> bool:
    """Check if a tool is a write tool (Edit, Write, Bash, NotebookEdit)."""
    return tool_name in ("Edit", "Write", "Bash", "NotebookEdit")