"""Tests for permissions/manager.py — check, ask_user, wildcard grants, persistence."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from next_code.permissions.manager import PermissionManager, PermissionDecision
from next_code.tools.base import ToolRegistry, create_default_registry


def test_read_only_tools_auto_allowed():
    """Read, Glob, Grep are allowed by default via is_read_only()."""
    with patch("next_code.permissions.manager.get_setting", return_value=[]):
        pm = PermissionManager()
        registry = create_default_registry()
        assert pm.check("Read", {"file_path": "/tmp/test"}, registry=registry) == PermissionDecision.ALLOW
        assert pm.check("Glob", {"pattern": "*.py"}, registry=registry) == PermissionDecision.ALLOW
        assert pm.check("Grep", {"pattern": "foo"}, registry=registry) == PermissionDecision.ALLOW


def test_read_only_tools_without_registry_fallback():
    """Without registry, read-only tools fall through to ASK."""
    with patch("next_code.permissions.manager.get_setting", return_value=[]):
        pm = PermissionManager()
        # No registry provided — can't determine is_read_only(), so ASK
        assert pm.check("Read", {"file_path": "/tmp/test"}) == PermissionDecision.ASK


def test_dangerous_tools_ask():
    """Bash, Edit, Write require permission by default."""
    with patch("next_code.permissions.manager.get_setting", return_value=[]):
        pm = PermissionManager()
        registry = create_default_registry()
        assert pm.check("Bash", {"command": "rm -rf /"}, registry=registry) == PermissionDecision.ASK
        assert pm.check("Edit", {"file_path": "/tmp/test.py"}, registry=registry) == PermissionDecision.ASK
        assert pm.check("Write", {"file_path": "/tmp/test.py"}, registry=registry) == PermissionDecision.ASK


def test_wildcard_session_grant():
    """Wildcard grant allows all operations for a tool."""
    with patch("next_code.permissions.manager.get_setting", return_value=[]):
        pm = PermissionManager()
        # Edit wildcard persists Edit:* to settings and matches all files
        pm.grant_session("Edit", {}, wildcard=True)
        assert pm.check("Edit", {"file_path": "/tmp/test.py"}) == PermissionDecision.ALLOW
        assert pm.check("Edit", {"file_path": "/home/user/secret.py"}) == PermissionDecision.ALLOW


def test_specific_session_grant():
    """Specific grant only allows the exact operation."""
    with patch("next_code.permissions.manager.get_setting", return_value=[]):
        pm = PermissionManager()
        pm.grant_session("Bash", {"command": "ls"})
        assert pm.check("Bash", {"command": "ls"}) == PermissionDecision.ALLOW
        assert pm.check("Bash", {"command": "rm -rf"}) == PermissionDecision.ASK


def test_deny_list_priority():
    """Deny list takes highest priority."""
    with patch("next_code.permissions.manager.get_setting", return_value=[]):
        pm = PermissionManager()
        pm._denied = ["Bash:*"]
        # Bash wildcard grant produces a session-only grant (no command param)
        # but deny list still takes priority
        pm.grant_session("Bash", {}, wildcard=True)
        assert pm.check("Bash", {"command": "ls"}) == PermissionDecision.DENY


def test_ask_user_method_exists():
    """ask_user method is available for interactive confirmation."""
    pm = PermissionManager()
    assert hasattr(pm, "ask_user")
    assert callable(pm.ask_user)


def test_wildcard_grant_persists_to_settings():
    """Wildcard grant calls update_setting to persist pattern."""
    with patch("next_code.permissions.manager.update_setting") as mock_update:
        with patch("next_code.permissions.manager.get_setting", return_value=[]):
            pm = PermissionManager()
            # Edit wildcard with no params persists Edit:* to settings
            pm.grant_session("Edit", {}, wildcard=True)

            # update_setting should be called with the allow list containing "Edit:*"
            mock_update.assert_called_once_with("permissions.allow", ["Edit:*"])

            # In-memory list also updated
            assert "Edit:*" in pm._allowed


def test_wildcard_grant_no_duplicate():
    """Persisting the same pattern twice doesn't create duplicates."""
    with patch("next_code.permissions.manager.update_setting") as mock_update:
        with patch("next_code.permissions.manager.get_setting", return_value=["Edit:*"]):
            pm = PermissionManager()
            pm.grant_session("Edit", {}, wildcard=True)

            # Should NOT call update_setting since "Edit:*" already exists
            mock_update.assert_not_called()

            # In-memory list should not duplicate
            assert pm._allowed.count("Edit:*") == 1


def test_specific_grant_does_not_persist():
    """Non-wildcard grant stays in memory only."""
    with patch("next_code.permissions.manager.update_setting") as mock_update:
        with patch("next_code.permissions.manager.get_setting", return_value=[]):
            pm = PermissionManager()
            pm.grant_session("Bash", {"command": "ls"})

            # update_setting should NOT be called for non-wildcard grants
            mock_update.assert_not_called()

            # But session-allowed has it
            assert "Bash:ls" in pm._session_allowed


def test_compound_command_grants_all_segments():
    """Wildcard grant on a compound command (&&) persists rules for each meaningful segment."""
    with patch("next_code.permissions.manager.update_setting") as mock_update:
        with patch("next_code.permissions.manager.get_setting", return_value=[]):
            pm = PermissionManager()
            result = pm.grant_session("Bash", {"command": "git add . && git commit -m 'fix'"}, wildcard=True)

            # Should return both prefixes
            assert "git commit" in result
            assert "git add" in result
            assert len(result) == 2

            # Both patterns should be persisted
            calls = mock_update.call_args_list
            persisted = []
            for call in calls:
                persisted.extend(call[0][1])
            assert "Bash(git add:*)" in persisted
            assert "Bash(git commit:*)" in persisted


def test_compound_command_three_segments():
    """Three-segment compound command persists rules for all meaningful segments."""
    with patch("next_code.permissions.manager.update_setting") as mock_update:
        with patch("next_code.permissions.manager.get_setting", return_value=[]):
            pm = PermissionManager()
            result = pm.grant_session(
                "Bash",
                {"command": "cd /tmp && npm run build && npm run test"},
                wildcard=True,
            )

            # get_command_prefix returns last meaningful prefix: "npm run"
            # _extract_prefix_single on segments: "cd /tmp" -> None (cd is setup),
            # "npm run build" -> "npm run", "npm run test" -> "npm run"
            # So we get "npm run" from get_command_prefix and one more from segments
            assert "npm run" in result

            # Both patterns should be in session_allowed
            assert "Bash:npm run:*" in pm._session_allowed


def test_compound_command_skip_setup_segments():
    """Setup commands (cd, source) in compound commands don't get their own rules."""
    with patch("next_code.permissions.manager.update_setting") as mock_update:
        with patch("next_code.permissions.manager.get_setting", return_value=[]):
            pm = PermissionManager()
            result = pm.grant_session("Bash", {"command": "cd /tmp && ls"}, wildcard=True)

            # "cd /tmp" yields no prefix (cd is setup), "ls" has no subcommand
            # get_command_prefix returns None for "ls" (single token)
            # So result should be empty
            assert result == []


def test_non_compound_single_prefix():
    """Non-compound command still works as before — returns single prefix."""
    with patch("next_code.permissions.manager.update_setting") as mock_update:
        with patch("next_code.permissions.manager.get_setting", return_value=[]):
            pm = PermissionManager()
            result = pm.grant_session("Bash", {"command": "git commit -m 'fix'"}, wildcard=True)

            assert result == ["git commit"]
            mock_update.assert_called_once_with("permissions.allow", ["Bash(git commit:*)"])
