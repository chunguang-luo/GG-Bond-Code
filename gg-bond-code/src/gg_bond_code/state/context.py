"""ToolUseContext — runtime context container for query/tool execution.

Mirrors Claude Code's ToolUseContext (Tool.ts:158-254):
a per-interaction context that decouples business logic from the
global Store. Different callers can provide different implementations
of get_state/set_state (e.g. no-op for isolated sub-agents).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from ..permissions.manager import PermissionManager
from ..tools.base import ToolRegistry


@dataclass
class ToolUseContext:
    """Runtime context for a single query or tool execution.

    Instead of directly accessing the global Store, QueryRunner and tools
    receive state through this container. This allows:
    - Main loop: connect to the real Store
    - Sub-agents: isolate with no-op set_state
    - Testing: inject mock state

    Mirrors Claude Code's design where getAppState/setAppState are
    functions rather than Store references, enabling per-call customization.
    """

    # ── State access ──────────────────────────────────────────────────
    get_state: Callable[[str], Any]
    """Read a state value by key. Always connected to real data."""

    set_state: Callable[[str, Any], None]
    """Write a state value. Can be no-op for isolated contexts."""

    set_state_for_tasks: Callable[[str, Any], None] | None = None
    """Write state that must always reach the root Store (e.g. task registration).
    Falls back to set_state if not provided.
    Mirrors Claude Code's setAppStateForTasks — always penetrates isolation."""

    # ── Core services ─────────────────────────────────────────────────
    permissions: PermissionManager = field(default_factory=PermissionManager)
    registry: ToolRegistry = field(default_factory=ToolRegistry)
    abort: asyncio.Event = field(default_factory=asyncio.Event)

    # ── Identity ──────────────────────────────────────────────────────
    agent_id: str | None = None
    agent_type: str | None = None

    def get_set_state_for_tasks(self) -> Callable[[str, Any], None]:
        """Get the task-penetrating setter, falling back to set_state."""
        return self.set_state_for_tasks or self.set_state


def create_store_context(
    store: Any = None,
    permissions: PermissionManager | None = None,
    registry: ToolRegistry | None = None,
) -> ToolUseContext:
    """Create a ToolUseContext backed by the global Store.

    This is the standard context for the main conversation loop.
    """
    if store is None:
        from .store import Store
        store = Store()

    return ToolUseContext(
        get_state=store.get,
        set_state=store.set,
        set_state_for_tasks=store.set,
        permissions=permissions or PermissionManager(),
        registry=registry or ToolRegistry(),
    )


def create_subagent_context(
    parent: ToolUseContext,
    *,
    share_abort: bool = False,
    share_set_state: bool = False,
    agent_id: str | None = None,
    agent_type: str | None = None,
) -> ToolUseContext:
    """Create an isolated ToolUseContext for a sub-agent.

    Mirrors Claude Code's createSubagentContext:
    - set_state defaults to no-op (sub-agents shouldn't modify UI state)
    - set_state_for_tasks always penetrates to the parent's root Store
    - abort can be shared or independent
    - permissions are isolated with shouldAvoidPermissionPrompts

    Args:
        parent: The parent context to derive from.
        share_abort: If True, share the parent's abort event.
        share_set_state: If True, share the parent's set_state (dangerous).
        agent_id: Optional agent identifier.
        agent_type: Optional agent type string.
    """
    # set_state: no-op by default, opt-in to share
    set_state = parent.set_state if share_set_state else _noop_set_state

    # set_state_for_tasks: ALWAYS penetrates to root — mirrors Claude Code's
    # "Task registration/kill must always reach the root store"
    set_state_for_tasks = parent.get_set_state_for_tasks()

    # abort: shared or independent
    abort = parent.abort if share_abort else asyncio.Event()

    return ToolUseContext(
        get_state=parent.get_state,
        set_state=set_state,
        set_state_for_tasks=set_state_for_tasks,
        permissions=parent.permissions,
        registry=parent.registry,
        abort=abort,
        agent_id=agent_id,
        agent_type=agent_type,
    )


def _noop_set_state(key: str, value: Any) -> None:
    """No-op setter for isolated sub-agent contexts."""
    pass
