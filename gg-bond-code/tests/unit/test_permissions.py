"""Tests for permissions/manager.py — check, ask_user, wildcard grants, persistence."""

import json
from pathlib import Path
from unittest.mock import patch

from gg_bond_code.permissions.manager import PermissionManager, PermissionDecision


def test_read_only_tools_auto_allowed():
    """Read, Glob, Grep are allowed by default."""
    pm = PermissionManager()
    assert pm.check("Read", {"file_path": "/tmp/test"}) == PermissionDecision.ALLOW
    assert pm.check("Glob", {"pattern": "*.py"}) == PermissionDecision.ALLOW
    assert pm.check("Grep", {"pattern": "foo"}) == PermissionDecision.ALLOW


def test_dangerous_tools_ask():
    """Bash, Edit, Write require permission by default."""
    pm = PermissionManager()
    assert pm.check("Bash", {"command": "rm -rf /"}) == PermissionDecision.ASK
    assert pm.check("Edit", {"file_path": "/tmp/test.py"}) == PermissionDecision.ASK
    assert pm.check("Write", {"file_path": "/tmp/test.py"}) == PermissionDecision.ASK


def test_wildcard_session_grant():
    """Wildcard grant allows all operations for a tool."""
    pm = PermissionManager()
    pm.grant_session("Bash", {}, wildcard=True)
    assert pm.check("Bash", {"command": "ls -la"}) == PermissionDecision.ALLOW
    assert pm.check("Bash", {"command": "rm -rf /"}) == PermissionDecision.ALLOW


def test_specific_session_grant():
    """Specific grant only allows the exact operation."""
    pm = PermissionManager()
    pm.grant_session("Bash", {"command": "ls"})
    assert pm.check("Bash", {"command": "ls"}) == PermissionDecision.ALLOW
    assert pm.check("Bash", {"command": "rm -rf"}) == PermissionDecision.ASK


def test_deny_list_priority():
    """Deny list takes highest priority."""
    pm = PermissionManager()
    pm._denied = ["Bash:*"]
    pm.grant_session("Bash", {}, wildcard=True)
    assert pm.check("Bash", {"command": "ls"}) == PermissionDecision.DENY


def test_ask_user_method_exists():
    """ask_user method is available for interactive confirmation."""
    pm = PermissionManager()
    assert hasattr(pm, "ask_user")
    assert callable(pm.ask_user)


def test_wildcard_grant_persists_to_settings(tmp_path: Path):
    """Wildcard grant writes pattern to project .settings.json."""
    settings_path = tmp_path / ".ggbond" / ".settings.json"
    # Start with empty settings
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("{}")

    with patch("gg_bond_code.permissions.manager._PROJECT_SETTINGS_PATH", settings_path):
        pm = PermissionManager()
        pm.grant_session("Bash", {}, wildcard=True)

        # Check file was written
        data = json.loads(settings_path.read_text())
        assert "Bash:*" in data["permissions"]["allow"]

        # In-memory list also updated
        assert "Bash:*" in pm._allowed


def test_wildcard_grant_no_duplicate(tmp_path: Path):
    """Persisting the same pattern twice doesn't create duplicates."""
    settings_path = tmp_path / ".ggbond" / ".settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text('{"permissions": {"allow": ["Bash:*"]}}')

    with patch("gg_bond_code.permissions.manager._PROJECT_SETTINGS_PATH", settings_path):
        pm = PermissionManager()
        pm.grant_session("Bash", {}, wildcard=True)

        data = json.loads(settings_path.read_text())
        assert data["permissions"]["allow"].count("Bash:*") == 1


def test_specific_grant_does_not_persist(tmp_path: Path):
    """Non-wildcard grant stays in memory only."""
    settings_path = tmp_path / ".ggbond" / ".settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("{}")

    with patch("gg_bond_code.permissions.manager._PROJECT_SETTINGS_PATH", settings_path):
        pm = PermissionManager()
        pm.grant_session("Bash", {"command": "ls"})

        # File should remain unchanged (no permissions key)
        data = json.loads(settings_path.read_text())
        assert "permissions" not in data

        # But session-allowed has it
        assert "Bash:ls" in pm._session_allowed
