"""Tests for permissions/modes.py — PermissionMode enum and switching."""

import pytest
from next_code.permissions.modes import (
    PermissionMode,
    get_next_permission_mode,
    is_write_tool,
)


class TestPermissionMode:
    def test_enum_values(self):
        assert PermissionMode.DEFAULT == "default"
        assert PermissionMode.PLAN == "plan"
        assert PermissionMode.ACCEPT_EDITS == "acceptEdits"
        assert PermissionMode.BYPASS_PERMISSIONS == "bypassPermissions"
        assert PermissionMode.DONT_ASK == "dontAsk"

    def test_enum_from_string(self):
        assert PermissionMode("default") == PermissionMode.DEFAULT
        assert PermissionMode("plan") == PermissionMode.PLAN


class TestGetNextPermissionMode:
    def test_default_to_accept_edits(self):
        assert get_next_permission_mode(PermissionMode.DEFAULT) == PermissionMode.ACCEPT_EDITS

    def test_accept_edits_to_plan(self):
        assert get_next_permission_mode(PermissionMode.ACCEPT_EDITS) == PermissionMode.PLAN

    def test_plan_to_default(self):
        assert get_next_permission_mode(PermissionMode.PLAN) == PermissionMode.DEFAULT

    def test_dont_ask_to_default(self):
        assert get_next_permission_mode(PermissionMode.DONT_ASK) == PermissionMode.DEFAULT

    def test_bypass_to_default(self):
        assert get_next_permission_mode(PermissionMode.BYPASS_PERMISSIONS) == PermissionMode.DEFAULT

    def test_with_bypass_available(self):
        """With bypass available, cycle includes bypass after plan."""
        assert get_next_permission_mode(
            PermissionMode.PLAN, bypass_available=True,
        ) == PermissionMode.BYPASS_PERMISSIONS

    def test_with_bypass_available_full_cycle(self):
        """Full cycle with bypass: default→acceptEdits→plan→bypass→default."""
        mode = get_next_permission_mode(PermissionMode.DEFAULT, bypass_available=True)
        assert mode == PermissionMode.ACCEPT_EDITS
        mode = get_next_permission_mode(mode, bypass_available=True)
        assert mode == PermissionMode.PLAN
        mode = get_next_permission_mode(mode, bypass_available=True)
        assert mode == PermissionMode.BYPASS_PERMISSIONS
        mode = get_next_permission_mode(mode, bypass_available=True)
        assert mode == PermissionMode.DEFAULT

    def test_unknown_mode_returns_default(self):
        """Unknown mode falls back to default."""
        # Simulate by passing a value not in the cycle
        result = get_next_permission_mode(PermissionMode.DONT_ASK)
        assert result == PermissionMode.DEFAULT


class TestIsWriteTool:
    def test_write_tools(self):
        assert is_write_tool("Edit")
        assert is_write_tool("Write")
        assert is_write_tool("Bash")
        assert is_write_tool("NotebookEdit")

    def test_read_tools(self):
        assert not is_write_tool("Read")
        assert not is_write_tool("Glob")
        assert not is_write_tool("Grep")
        assert not is_write_tool("WebSearch")