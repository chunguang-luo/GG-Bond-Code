"""Tests for permissions/path_validation.py — path validation and safety checks."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from next_code.permissions.path_validation import (
    validate_path,
    is_dangerous_removal_path,
    is_path_allowed,
    check_path_safety_for_auto_edit,
    expand_tilde,
    contains_vulnerable_unc_path,
    _is_in_working_dir,
    _is_in_protected_dir,
)


class TestValidatePath:
    def test_normal_path(self):
        allowed, reason = validate_path("/tmp/test.py", "/tmp", "write")
        assert allowed

    def test_shell_expansion_dollar(self):
        allowed, reason = validate_path("/tmp/$USER/test", "/tmp", "write")
        assert not allowed
        assert "Shell expansion" in reason

    def test_shell_expansion_percent(self):
        allowed, reason = validate_path("%TEMP%/test", "/tmp", "write")
        assert not allowed
        assert "Shell expansion" in reason

    def test_unc_path(self):
        allowed, reason = validate_path("\\\\server\\share", "/tmp", "write")
        assert not allowed
        assert "UNC" in reason

    def test_tilde_expansion_user(self):
        allowed, reason = validate_path("~otheruser/test", "/tmp", "write")
        assert not allowed
        assert "Tilde expansion" in reason

    def test_tilde_home(self):
        allowed, reason = validate_path("~/test.py", "/tmp", "write")
        assert allowed

    def test_tilde_alone(self):
        allowed, reason = validate_path("~", "/tmp", "read")
        assert allowed

    def test_glob_in_write(self):
        allowed, reason = validate_path("/tmp/*.py", "/tmp", "write")
        assert not allowed
        assert "Glob" in reason

    def test_glob_in_read(self):
        allowed, reason = validate_path("/tmp/*.py", "/tmp", "read")
        assert allowed

    def test_equals_prefix(self):
        allowed, reason = validate_path("=/usr/bin", "/tmp", "write")
        assert not allowed
        assert "Shell expansion" in reason


class TestIsDangerousRemovalPath:
    def test_root(self):
        assert is_dangerous_removal_path("/")

    def test_wildcard(self):
        assert is_dangerous_removal_path("*")
        assert is_dangerous_removal_path("/tmp/*")

    def test_home_directory(self):
        assert is_dangerous_removal_path(str(Path.home()))

    def test_root_child(self):
        assert is_dangerous_removal_path("/usr")
        assert is_dangerous_removal_path("/tmp")
        assert is_dangerous_removal_path("/etc")

    def test_safe_path(self):
        assert not is_dangerous_removal_path("/tmp/some-subdir")
        assert not is_dangerous_removal_path(str(Path.home() / "projects" / "test"))


class TestIsPathAllowed:
    def test_read_in_working_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.py")
            Path(filepath).touch()
            allowed, reason = is_path_allowed(filepath, tmpdir, "read")
            assert allowed

    def test_write_in_working_dir_default_mode(self):
        """In default mode, write in working dir still requires rules."""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.py")
            allowed, reason = is_path_allowed(filepath, tmpdir, "write")
            # Default mode does NOT auto-allow writes in working dir
            # (only acceptEdits does)
            assert not allowed

    def test_write_in_working_dir_accept_edits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.py")
            allowed, reason = is_path_allowed(
                filepath, tmpdir, "write", permission_mode="acceptEdits",
            )
            assert allowed

    def test_write_outside_working_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            outside = "/tmp/outside_test_file.txt"
            allowed, reason = is_path_allowed(outside, tmpdir, "write")
            assert not allowed

    def test_protected_dir_denied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.makedirs(git_dir, exist_ok=True)
            config = os.path.join(git_dir, "config")
            Path(config).touch()
            allowed, reason = is_path_allowed(config, tmpdir, "write")
            assert not allowed
            assert "protected" in reason.lower()

    def test_deny_rule_priority(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "secret.py")
            Path(filepath).touch()
            allowed, reason = is_path_allowed(
                filepath, tmpdir, "read",
                deny_rules=["secret.py"],
            )
            assert not allowed

    def test_allow_rule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            outside = "/tmp/special_file.txt"
            allowed, reason = is_path_allowed(
                outside, tmpdir, "write",
                allow_rules=["/tmp/special_file.txt"],
            )
            assert allowed


class TestCheckPathSafety:
    def test_git_dir_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            git_config = os.path.join(tmpdir, ".git", "config")
            os.makedirs(os.path.dirname(git_config), exist_ok=True)
            Path(git_config).touch()
            safe, reason = check_path_safety_for_auto_edit(git_config, tmpdir)
            assert not safe

    def test_claude_dir_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_file = os.path.join(tmpdir, ".claude", "settings.json")
            os.makedirs(os.path.dirname(claude_file), exist_ok=True)
            Path(claude_file).touch()
            safe, reason = check_path_safety_for_auto_edit(claude_file, tmpdir)
            assert not safe

    def test_normal_file_safe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            normal = os.path.join(tmpdir, "src", "main.py")
            os.makedirs(os.path.dirname(normal), exist_ok=True)
            Path(normal).touch()
            safe, reason = check_path_safety_for_auto_edit(normal, tmpdir)
            assert safe


class TestIsInWorkingDir:
    def test_inside(self):
        assert _is_in_working_dir("/home/user/proj/src/main.py", "/home/user/proj")

    def test_outside(self):
        assert not _is_in_working_dir("/etc/passwd", "/home/user/proj")

    def test_exact_match(self):
        assert _is_in_working_dir("/home/user/proj", "/home/user/proj")


class TestIsInProtectedDir:
    def test_git(self):
        assert _is_in_protected_dir("/home/user/proj/.git/config", "/home/user/proj")

    def test_claude(self):
        assert _is_in_protected_dir(
            "/home/user/proj/.claude/settings.json", "/home/user/proj",
        )

    def test_vscode(self):
        assert _is_in_protected_dir(
            "/home/user/proj/.vscode/settings.json", "/home/user/proj",
        )

    def test_normal(self):
        assert not _is_in_protected_dir("/home/user/proj/src/main.py", "/home/user/proj")


class TestExpandTilde:
    def test_tilde_alone(self):
        assert expand_tilde("~") == str(Path.home())

    def test_tilde_slash(self):
        assert expand_tilde("~/Documents") == str(Path.home() / "Documents")

    def test_no_tilde(self):
        assert expand_tilde("/usr/bin") == "/usr/bin"


class TestContainsVulnerableUncPath:
    def test_unc_path(self):
        assert contains_vulnerable_unc_path("\\\\server\\share")

    def test_normal_path(self):
        assert not contains_vulnerable_unc_path("/usr/bin")