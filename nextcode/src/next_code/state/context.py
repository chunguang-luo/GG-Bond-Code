"""ToolUseContext — runtime context container for query/tool execution.

Mirrors Claude Code's ToolUseContext (Tool.ts:158-254):
a per-interaction context that decouples business logic from the
global Store. Different callers can provide different implementations
of get_state/set_state (e.g. no-op for isolated sub-agents).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..permissions.manager import PermissionManager
from ..tools.base import ToolRegistry
from ..compact.file_cache import FileStateCache


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
    file_cache: FileStateCache = field(default_factory=FileStateCache)
    command_registry: Any = None  # CommandRegistry — use Any to avoid circular import

    # ── Identity ──────────────────────────────────────────────────────
    agent_id: str | None = None
    agent_type: str | None = None

    # ── Event forwarding ─────────────────────────────────────────────
    emit_event: Callable[[Any], None] | None = None
    """Forward events from sub-tools (e.g. AgentTool) to the QueryRunner event loop.
    Set by QueryRunner.run() so that tools can yield real-time events that
    flow through to IPCBridge and the frontend."""

    emit_ipc: Callable[[Any], Any] | None = None
    """Direct IPC emit callback — bypasses the QueryRunner yield loop entirely.
    Set by IPCBridge so that long-running sub-tools (e.g. AgentTool) can
    stream events to the frontend in real-time, without waiting for the
    tool_result to be yielded."""

    agent_depth: int = 0
    """Current Agent nesting depth. 0 = main agent, 1 = sub-agent, 2 = sub-sub-agent.
    Used to enforce a maximum nesting depth of 2."""

    critical_reminder: str | None = None
    """If set, injected before every user message in the conversation loop.
    Used by agents like Verification to prevent constraint drift in long conversations."""

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
    If no registry is provided, loads all default tools.
    """
    if store is None:
        from .store import Store
        store = Store()

    if registry is None:
        from ..tools.base import create_default_registry
        registry = create_default_registry()

    pm = permissions or PermissionManager()
    # Set working directory from store for path-based permission checks
    project_root = store.get("project_root", str(Path.cwd())) if store else str(Path.cwd())
    pm.cwd = project_root

    return ToolUseContext(
        get_state=store.get,
        set_state=store.set,
        set_state_for_tasks=store.set,
        permissions=pm,
        registry=registry,
    )


def create_subagent_context(
    parent: ToolUseContext,
    *,
    share_abort: bool = False,
    share_set_state: bool = False,
    share_metrics: bool = False,
    agent_id: str | None = None,
    agent_type: str | None = None,
    permission_mode: str | None = None,
    allowed_tools: list[str] | None = None,
    avoid_permission_prompts: bool = True,
) -> ToolUseContext:
    """Create an isolated ToolUseContext for a sub-agent.

    Mirrors Claude Code's createSubagentContext:
    - set_state defaults to no-op (sub-agents shouldn't modify UI state)
    - set_state_for_tasks always penetrates to the parent's root Store
    - abort can be shared or independent
    - permissions are isolated with avoid_permission_prompts

    Args:
        parent: The parent context to derive from.
        share_abort: If True, share the parent's abort event.
        share_set_state: If True, share the parent's set_state (dangerous).
        share_metrics: If True, share API metrics reporting.
        agent_id: Optional agent identifier.
        agent_type: Optional agent type string.
        permission_mode: Override permission mode (e.g. 'plan' for read-only).
        allowed_tools: Replace session-level allowed tools for this agent.
        avoid_permission_prompts: Auto-deny permission prompts (default True
            for non-interactive sub-agents).
    """
    # set_state: no-op by default, opt-in to share
    set_state = parent.set_state if share_set_state else _noop_set_state

    # set_state_for_tasks: ALWAYS penetrates to root — mirrors Claude Code's
    # "Task registration/kill must always reach the root store"
    set_state_for_tasks = parent.get_set_state_for_tasks()

    # abort: shared or independent
    abort = parent.abort if share_abort else asyncio.Event()

    # permissions: wrap for sub-agent isolation
    # Sub-agent permission decisions must not leak back to the parent.
    permissions = _wrap_permissions(
        parent.permissions,
        mode=permission_mode,
        allowed_tools=allowed_tools,
        avoid_prompts=avoid_permission_prompts,
    )

    return ToolUseContext(
        get_state=parent.get_state,
        set_state=set_state,
        set_state_for_tasks=set_state_for_tasks,
        permissions=permissions,
        registry=parent.registry,
        abort=abort,
        file_cache=parent.file_cache.clone(),
        agent_id=agent_id,
        agent_type=agent_type,
        emit_ipc=parent.emit_ipc,  # 继承父级的 IPC 直通，支持嵌套实时输出
        agent_depth=parent.agent_depth + 1,  # 嵌套深度 +1
    )


def _wrap_permissions(
    parent: PermissionManager,
    *,
    mode: str | None = None,
    allowed_tools: list[str] | None = None,
    avoid_prompts: bool = True,
) -> PermissionManager:
    """Wrap permissions for a sub-agent.

    Creates a new PermissionManager (not sharing state with the parent)
    so sub-agent permission decisions don't leak back.
    """
    wrapped = PermissionManager(
        mode=mode or parent.mode,
        avoid_permission_prompts=avoid_prompts,
    )

    # Inherit parent's persistent allow/deny/ask rules
    wrapped._allowed = list(parent._allowed)
    wrapped._denied = list(parent._denied)
    wrapped._ask_rules = list(parent._ask_rules)

    # Inherit working directory for path checks
    wrapped.cwd = parent.cwd

    # Sub-agents that can't prompt the user are headless
    wrapped.is_headless = avoid_prompts

    if allowed_tools is not None:
        wrapped.set_session_allowed_tools(allowed_tools)

    return wrapped


def _noop_set_state(key: str, value: Any) -> None:
    """No-op setter for isolated sub-agent contexts."""
    pass
