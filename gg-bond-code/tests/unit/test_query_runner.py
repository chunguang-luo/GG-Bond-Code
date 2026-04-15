"""Tests for query.py — QueryRunner initialization and permission callback."""

import asyncio
from unittest.mock import patch

from gg_bond_code.query import QueryRunner, QueryEvent
from gg_bond_code.permissions.manager import PermissionDecision


def test_query_runner_store_reuse():
    """QueryRunner reuses store instance from __init__."""
    runner = QueryRunner(model="deepseek-chat")
    assert hasattr(runner, "store")
    assert runner.store is not None


def test_query_runner_default_no_callback():
    """QueryRunner has no permission callback by default."""
    runner = QueryRunner(model="deepseek-chat")
    assert runner._permission_callback is None


def test_query_runner_with_callback():
    """QueryRunner accepts a permission callback."""
    async def mock_callback(tool_name, params):
        return PermissionDecision.ALLOW

    runner = QueryRunner(model="deepseek-chat", permission_callback=mock_callback)
    assert runner._permission_callback is not None


def test_query_event_types():
    """QueryEvent supports all expected event types."""
    assert QueryEvent(type="text", content="hello").type == "text"
    assert QueryEvent(type="thinking", content="hmm").type == "thinking"
    assert QueryEvent(type="tool_start", tool_name="Bash").type == "tool_start"
    assert QueryEvent(type="tool_use", tool_name="Bash").type == "tool_use"
    assert QueryEvent(type="tool_result", tool_name="Bash").type == "tool_result"
    assert QueryEvent(type="error", content="fail").type == "error"


def test_check_permission_deny_without_callback():
    """ASK decision becomes DENY when no callback is set (print mode)."""
    with patch("gg_bond_code.query.PermissionManager") as MockPM:
        pm = MockPM.return_value
        pm.check.return_value = PermissionDecision.ASK
        runner = QueryRunner(model="deepseek-chat")
        runner.permissions = pm
        result = asyncio.run(runner._check_permission("Bash", {"command": "rm -rf /"}))
        assert result == PermissionDecision.DENY


def test_check_permission_uses_callback():
    """ASK decision delegates to callback when available."""
    async def allow_all(tool_name, params):
        return PermissionDecision.ALLOW

    with patch("gg_bond_code.query.PermissionManager") as MockPM:
        pm = MockPM.return_value
        pm.check.return_value = PermissionDecision.ASK
        runner = QueryRunner(model="deepseek-chat", permission_callback=allow_all)
        runner.permissions = pm
        result = asyncio.run(runner._check_permission("Bash", {"command": "rm -rf /"}))
        assert result == PermissionDecision.ALLOW


def test_check_permission_read_only_allowed():
    """Read-only tools are allowed without callback."""
    with patch("gg_bond_code.query.PermissionManager") as MockPM:
        pm = MockPM.return_value
        pm.check.return_value = PermissionDecision.ALLOW
        runner = QueryRunner(model="deepseek-chat")
        runner.permissions = pm
        result = asyncio.run(runner._check_permission("Read", {"file_path": "/tmp/test"}))
        assert result == PermissionDecision.ALLOW
