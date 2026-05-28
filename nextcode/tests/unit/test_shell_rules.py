"""Tests for permissions/shell_rules.py — rule parsing and shell matching."""

import pytest
from next_code.permissions.shell_rules import (
    parse_rule_string,
    match_shell_rule,
    _find_first_unescaped,
    _unescape_rule_content,
)


class TestParseRuleString:
    def test_plain_tool_name(self):
        assert parse_rule_string("Bash") == ("Bash", None)

    def test_tool_with_content(self):
        assert parse_rule_string("Bash(git commit:*)") == ("Bash", "git commit:*")

    def test_tool_with_empty_parens(self):
        assert parse_rule_string("Bash()") == ("Bash", None)

    def test_tool_with_star_parens(self):
        assert parse_rule_string("Bash(*)") == ("Bash", None)

    def test_tool_with_escaped_parens(self):
        assert parse_rule_string(r"Bash(npm run \(dev\))") == ("Bash", "npm run (dev)")

    def test_mcp_tool(self):
        assert parse_rule_string("mcp__server1") == ("mcp__server1", None)

    def test_legacy_task_to_agent(self):
        assert parse_rule_string("Task") == ("Agent", None)

    def test_legacy_kill_shell(self):
        assert parse_rule_string("KillShell") == ("TaskStop", None)

    # Session grant format: ToolName:content
    def test_session_grant_wildcard(self):
        assert parse_rule_string("Edit:*") == ("Edit", None)

    def test_session_grant_bash_content(self):
        assert parse_rule_string("Bash:git commit:*") == ("Bash", "git commit:*")

    def test_session_grant_write_wildcard(self):
        assert parse_rule_string("Write:*") == ("Write", None)

    def test_session_grant_plain_content(self):
        assert parse_rule_string("Bash:npm install") == ("Bash", "npm install")


class TestMatchShellRule:
    def test_exact_match(self):
        assert match_shell_rule("git status", "git status")
        assert not match_shell_rule("git status", "git push")

    def test_prefix_match(self):
        assert match_shell_rule("npm:*", "npm install")
        assert match_shell_rule("npm:*", "npm run build")
        assert not match_shell_rule("npm:*", "npx")

    def test_prefix_match_exact(self):
        """Prefix matches the base command itself."""
        assert match_shell_rule("npm:*", "npm")

    def test_wildcard_match(self):
        assert match_shell_rule("git *", "git add")
        assert match_shell_rule("git *", "git commit -m 'test'")

    def test_wildcard_match_base_command(self):
        """git * also matches just 'git' (trailing space optional)."""
        assert match_shell_rule("git *", "git")

    def test_whole_tool_rule(self):
        """Empty content or * matches everything."""
        assert match_shell_rule("*", "anything")
        assert match_shell_rule("", "anything")

    def test_none_content(self):
        """None content is whole-tool — handled at higher level."""
        # match_shell_rule with None won't be called, but test the guard
        pass


class TestFindFirstUnescaped:
    def test_simple(self):
        assert _find_first_unescaped("hello(world", "(") == 5

    def test_escaped(self):
        assert _find_first_unescaped(r"hello\(world", "(") == -1

    def test_double_escape(self):
        # \\ = literal backslash, then ( is unescaped
        assert _find_first_unescaped(r"hello\\(world", "(") == 7

    def test_not_found(self):
        assert _find_first_unescaped("hello", "(") == -1


class TestUnescapeRuleContent:
    def test_escaped_parens(self):
        assert _unescape_rule_content(r"npm run \(dev\)") == "npm run (dev)"

    def test_escaped_star(self):
        assert _unescape_rule_content(r"git add \*") == "git add *"

    def test_escaped_backslash(self):
        assert _unescape_rule_content(r"path\\to") == "path\\to"

    def test_no_escapes(self):
        assert _unescape_rule_content("git commit") == "git commit"