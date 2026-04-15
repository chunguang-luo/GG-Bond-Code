"""Tests for permissions/manager.py — check, ask_user, wildcard grants, persistence."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from gg_bond_code.permissions.manager import PermissionManager, PermissionDecision


def test_read_only_tools_auto_allowed():
    """Read, Glob, Grep are allowed by default."""
    with patch("gg_bond_code.permissions.manager.get_setting", return_value=[]):
        pm = PermissionManager()
        assert pm.check("Read", {"file_path": "/tmp/test"}) == PermissionDecision.ALLOW
        assert pm.check("Glob", {"pattern": "*.py"}) == PermissionDecision.ALLOW
        assert pm.check("Grep", {"pattern": "foo"}) == PermissionDecision.ALLOW


def test_dangerous_tools_ask():
    """Bash, Edit, Write require permission by default."""
    with patch("gg_bond_code.permissions.manager.get_setting", return_value=[]):
        pm = PermissionManager()
        assert pm.check("Bash", {"command": "rm -rf /"}) == PermissionDecision.ASK
        assert pm.check("Edit", {"file_path": "/tmp/test.py"}) == PermissionDecision.ASK
        assert pm.check("Write", {"file_path": "/tmp/test.py"}) == PermissionDecision.ASK


def test_wildcard_session_grant():
    """Wildcard grant allows all operations for a tool."""
    with patch("gg_bond_code.permissions.manager.get_setting", return_value=[]):
        pm = PermissionManager()
        pm.grant_session("Bash", {}, wildcard=True)
        assert pm.check("Bash", {"command": "ls -la"}) == PermissionDecision.ALLOW
        assert pm.check("Bash", {"command": "rm -rf /"}) == PermissionDecision.ALLOW


def test_specific_session_grant():
    """Specific grant only allows the exact operation."""
    with patch("gg_bond_code.permissions.manager.get_setting", return_value=[]):
        pm = PermissionManager()
        pm.grant_session("Bash", {"command": "ls"})
        assert pm.check("Bash", {"command": "ls"}) == PermissionDecision.ALLOW
        assert pm.check("Bash", {"command": "rm -rf"}) == PermissionDecision.ASK


def test_deny_list_priority():
    """Deny list takes highest priority."""
    with patch("gg_bond_code.permissions.manager.get_setting", return_value=[]):
        pm = PermissionManager()
        pm._denied = ["Bash:*"]
        pm.grant_session("Bash", {}, wildcard=True)
        assert pm.check("Bash", {"command": "ls"}) == PermissionDecision.DENY


def test_ask_user_method_exists():
    """ask_user method is available for interactive confirmation."""
    pm = PermissionManager()
    assert hasattr(pm, "ask_user")
    assert callable(pm.ask_user)


def test_wildcard_grant_persists_to_settings():
    """Wildcard grant calls update_setting to persist pattern."""
    with patch("gg_bond_code.permissions.manager.update_setting") as mock_update:
        with patch("gg_bond_code.permissions.manager.get_setting", return_value=[]):
            pm = PermissionManager()
            pm.grant_session("Bash", {}, wildcard=True)

            # update_setting should be called with the allow list containing "Bash:*"
            mock_update.assert_called_once_with("permissions.allow", ["Bash:*"])

            # In-memory list also updated
            assert "Bash:*" in pm._allowed


def test_wildcard_grant_no_duplicate():
    """Persisting the same pattern twice doesn't create duplicates."""
    with patch("gg_bond_code.permissions.manager.update_setting") as mock_update:
        with patch("gg_bond_code.permissions.manager.get_setting", return_value=["Bash:*"]):
            pm = PermissionManager()
            pm.grant_session("Bash", {}, wildcard=True)

            # Should NOT call update_setting since "Bash:*" already exists
            mock_update.assert_not_called()

            # In-memory list should not duplicate
            assert pm._allowed.count("Bash:*") == 1


def test_specific_grant_does_not_persist():
    """Non-wildcard grant stays in memory only."""
    with patch("gg_bond_code.permissions.manager.update_setting") as mock_update:
        with patch("gg_bond_code.permissions.manager.get_setting", return_value=[]):
            pm = PermissionManager()
            pm.grant_session("Bash", {"command": "ls"})

            # update_setting should NOT be called for non-wildcard grants
            mock_update.assert_not_called()

            # But session-allowed has it
            assert "Bash:ls" in pm._session_allowed
