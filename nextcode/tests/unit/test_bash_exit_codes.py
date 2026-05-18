"""Tests for tools/bash_exit_codes.py — exit code semantic interpretation."""

from next_code.tools.bash_exit_codes import interpret_exit_code, ExitCodeSemantic


class TestGrep:
    def test_grep_found(self):
        """grep exit 0 = found matches."""
        result = interpret_exit_code("grep pattern file.txt", 0)
        assert result.is_error is False
        assert result.message is None

    def test_grep_no_matches(self):
        """grep exit 1 = no matches found (not an error)."""
        result = interpret_exit_code("grep pattern file.txt", 1)
        assert result.is_error is False
        assert result.message == "No matches found"

    def test_grep_error(self):
        """grep exit 2+ = actual error."""
        result = interpret_exit_code("grep pattern file.txt", 2)
        assert result.is_error is True


class TestRgAg:
    """rg and ag follow the same convention as grep."""

    def test_rg_no_matches(self):
        result = interpret_exit_code("rg pattern", 1)
        assert result.is_error is False
        assert result.message == "No matches found"

    def test_ag_no_matches(self):
        result = interpret_exit_code("ag pattern", 1)
        assert result.is_error is False
        assert result.message == "No matches found"


class TestDiff:
    def test_diff_same(self):
        result = interpret_exit_code("diff a b", 0)
        assert result.is_error is False
        assert result.message is None

    def test_diff_differ(self):
        """diff exit 1 = files differ (not an error)."""
        result = interpret_exit_code("diff a b", 1)
        assert result.is_error is False
        assert result.message == "Files differ"

    def test_diff_error(self):
        result = interpret_exit_code("diff a b", 2)
        assert result.is_error is True


class TestTest:
    def test_test_true(self):
        result = interpret_exit_code("test -f file", 0)
        assert result.is_error is False
        assert result.message is None

    def test_test_false(self):
        """test exit 1 = condition false (not an error)."""
        result = interpret_exit_code("test -f file", 1)
        assert result.is_error is False
        assert result.message == "Condition is false"

    def test_test_error(self):
        result = interpret_exit_code("test -f file", 2)
        assert result.is_error is True


class TestGit:
    def test_git_success(self):
        result = interpret_exit_code("git status", 0)
        assert result.is_error is False

    def test_git_exit_1(self):
        """git exit 1 = expected condition, not an error."""
        result = interpret_exit_code("git diff", 1)
        assert result.is_error is False
        assert result.message is not None

    def test_git_error(self):
        result = interpret_exit_code("git push", 128)
        assert result.is_error is True


class TestDefault:
    def test_unknown_command_exit_0(self):
        result = interpret_exit_code("random_cmd", 0)
        assert result.is_error is False

    def test_unknown_command_exit_1(self):
        """Unknown commands: non-zero = error by default."""
        result = interpret_exit_code("random_cmd", 1)
        assert result.is_error is True

    def test_empty_command(self):
        result = interpret_exit_code("", 1)
        assert result.is_error is True


class TestPathPrefix:
    def test_path_prefixed_grep(self):
        result = interpret_exit_code("/usr/bin/grep pattern", 1)
        assert result.is_error is False
        assert result.message == "No matches found"
