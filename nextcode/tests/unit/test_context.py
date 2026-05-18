"""Tests for state/context.py — ToolUseContext, create_store_context, create_subagent_context."""

import asyncio
from unittest.mock import patch

from next_code.state.context import (
    ToolUseContext,
    create_store_context,
    create_subagent_context,
    _noop_set_state,
)
from next_code.state.store import _Store, reset_store
from next_code.permissions.manager import PermissionManager, PermissionDecision
from next_code.tools.base import ToolRegistry


# ── ToolUseContext basic ──────────────────────────────────────────────

def test_context_get_set_state():
    """Context delegates get/set to provided functions."""
    data = {"model": "deepseek-chat"}
    ctx = ToolUseContext(
        get_state=lambda k: data.get(k),
        set_state=lambda k, v: data.__setitem__(k, v),
    )
    assert ctx.get_state("model") == "deepseek-chat"
    ctx.set_state("model", "claude-sonnet")
    assert data["model"] == "claude-sonnet"


def test_context_noop_set_state():
    """_noop_set_state does nothing."""
    _noop_set_state("key", "value")  # should not raise


def test_context_set_state_for_tasks_fallback():
    """set_state_for_tasks falls back to set_state when not provided."""
    calls = []
    ctx = ToolUseContext(
        get_state=lambda k: None,
        set_state=lambda k, v: calls.append((k, v)),
    )
    assert ctx.get_set_state_for_tasks() is ctx.set_state
    ctx.get_set_state_for_tasks()("messages", [])
    assert calls == [("messages", [])]


def test_context_set_state_for_tasks_explicit():
    """set_state_for_tasks uses explicit function when provided."""
    set_calls = []
    task_calls = []
    ctx = ToolUseContext(
        get_state=lambda k: None,
        set_state=lambda k, v: set_calls.append((k, v)),
        set_state_for_tasks=lambda k, v: task_calls.append((k, v)),
    )
    ctx.set_state("model", "x")  # goes to regular setter
    ctx.get_set_state_for_tasks()("tasks", "y")  # goes to tasks setter
    assert set_calls == [("model", "x")]
    assert task_calls == [("tasks", "y")]


def test_context_abort_default():
    """Context has a default abort event."""
    ctx = ToolUseContext(
        get_state=lambda k: None,
        set_state=lambda k, v: None,
    )
    assert isinstance(ctx.abort, asyncio.Event)
    assert not ctx.abort.is_set()


def test_context_agent_identity():
    """Context carries agent identity."""
    ctx = ToolUseContext(
        get_state=lambda k: None,
        set_state=lambda k, v: None,
        agent_id="agent-1",
        agent_type="explore",
    )
    assert ctx.agent_id == "agent-1"
    assert ctx.agent_type == "explore"


# ── create_store_context ──────────────────────────────────────────────

def test_create_store_context_default():
    """create_store_context uses the global Store by default."""
    reset_store()
    ctx = create_store_context()
    # Should be able to read from the global store
    assert ctx.get_state is not None
    assert ctx.set_state is not None


def test_create_store_context_custom_store():
    """create_store_context uses provided store."""
    store = _Store()
    store.set("model", "test-model")
    ctx = create_store_context(store=store)
    assert ctx.get_state("model") == "test-model"


def test_create_store_context_custom_permissions():
    """create_store_context uses provided PermissionManager."""
    with patch("next_code.state.context.PermissionManager") as MockPM:
        pm = MockPM.return_value
        ctx = create_store_context(permissions=pm)
        assert ctx.permissions is pm


def test_create_store_context_custom_registry():
    """create_store_context uses provided ToolRegistry."""
    reg = ToolRegistry()
    ctx = create_store_context(registry=reg)
    assert ctx.registry is reg


def test_create_store_context_tasks_penetrates():
    """In store context, set_state_for_tasks writes to the same store."""
    store = _Store()
    ctx = create_store_context(store=store)
    # Both set_state and set_state_for_tasks should write to the store
    ctx.set_state("key1", "val1")
    ctx.get_set_state_for_tasks()("key2", "val2")
    assert store.get("key1") == "val1"
    assert store.get("key2") == "val2"


# ── create_subagent_context ───────────────────────────────────────────

def test_subagent_set_state_is_noop_by_default():
    """Sub-agent context has no-op set_state by default."""
    parent = ToolUseContext(
        get_state=lambda k: "parent-value",
        set_state=lambda k, v: None,
    )
    sub = create_subagent_context(parent)

    # set_state should be no-op — calling it does nothing
    sub.set_state("model", "sub-model")  # should not raise or affect parent


def test_subagent_get_state_reads_parent():
    """Sub-agent reads state from parent."""
    parent = ToolUseContext(
        get_state=lambda k: "parent-value",
        set_state=lambda k, v: None,
    )
    sub = create_subagent_context(parent)
    assert sub.get_state("model") == "parent-value"


def test_subagent_tasks_always_penetrate():
    """set_state_for_tasks always reaches the parent's root Store."""
    task_writes = []
    parent = ToolUseContext(
        get_state=lambda k: None,
        set_state=lambda k, v: task_writes.append((k, v)),
    )
    sub = create_subagent_context(parent)

    # Regular set_state is no-op
    sub.set_state("model", "x")
    assert task_writes == []

    # But tasks setter penetrates
    sub.get_set_state_for_tasks()("tasks", "register")
    assert task_writes == [("tasks", "register")]


def test_subagent_share_set_state():
    """share_set_state=True lets sub-agent modify parent state."""
    writes = []
    parent = ToolUseContext(
        get_state=lambda k: None,
        set_state=lambda k, v: writes.append((k, v)),
    )
    sub = create_subagent_context(parent, share_set_state=True)

    sub.set_state("model", "sub-model")
    assert writes == [("model", "sub-model")]


def test_subagent_share_abort():
    """share_abort=True shares the parent's abort event."""
    parent = ToolUseContext(
        get_state=lambda k: None,
        set_state=lambda k, v: None,
    )
    sub = create_subagent_context(parent, share_abort=True)
    assert sub.abort is parent.abort


def test_subagent_independent_abort():
    """share_abort=False gives sub-agent its own abort event."""
    parent = ToolUseContext(
        get_state=lambda k: None,
        set_state=lambda k, v: None,
    )
    sub = create_subagent_context(parent, share_abort=False)
    assert sub.abort is not parent.abort


def test_subagent_identity():
    """Sub-agent carries its own identity."""
    parent = ToolUseContext(
        get_state=lambda k: None,
        set_state=lambda k, v: None,
    )
    sub = create_subagent_context(parent, agent_id="explore-1", agent_type="explore")
    assert sub.agent_id == "explore-1"
    assert sub.agent_type == "explore"


def test_subagent_shares_permissions_and_registry():
    """Sub-agent shares parent's permissions and registry."""
    pm = PermissionManager()
    reg = ToolRegistry()
    parent = ToolUseContext(
        get_state=lambda k: None,
        set_state=lambda k, v: None,
        permissions=pm,
        registry=reg,
    )
    sub = create_subagent_context(parent)
    assert sub.permissions is pm
    assert sub.registry is reg


def test_nested_subagent_tasks_penetrate():
    """Tasks setter penetrates through multiple levels of nesting."""
    root_writes = []
    root = ToolUseContext(
        get_state=lambda k: None,
        set_state=lambda k, v: root_writes.append(("root", k, v)),
    )
    sub1 = create_subagent_context(root)
    sub2 = create_subagent_context(sub1)

    # Both levels have no-op set_state
    sub2.set_state("model", "x")
    assert root_writes == []

    # But tasks setter penetrates all the way to root
    sub2.get_set_state_for_tasks()("tasks", "deep-nested")
    assert root_writes == [("root", "tasks", "deep-nested")]
