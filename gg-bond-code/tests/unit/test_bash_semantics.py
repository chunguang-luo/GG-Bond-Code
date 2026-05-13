"""Tests for tools/bash_semantics.py — command semantic classification."""

from gg_bond_code.tools.bash_semantics import (
    classify_command,
    classify_pipeline,
    is_silent_command,
    CommandSemantic,
    BASH_SEARCH_COMMANDS,
    BASH_READ_COMMANDS,
    BASH_LIST_COMMANDS,
    BASH_SILENT_COMMANDS,
    BASH_NEUTRAL_COMMANDS,
)


# --- classify_command ---


class TestClassifyCommand:
    def test_search_commands(self):
        for cmd in BASH_SEARCH_COMMANDS:
            assert classify_command(cmd) == CommandSemantic.SEARCH, f"{cmd} should be SEARCH"

    def test_read_commands(self):
        for cmd in BASH_READ_COMMANDS:
            assert classify_command(cmd) == CommandSemantic.READ, f"{cmd} should be READ"

    def test_list_commands(self):
        for cmd in BASH_LIST_COMMANDS:
            assert classify_command(cmd) == CommandSemantic.LIST, f"{cmd} should be LIST"

    def test_silent_commands(self):
        for cmd in BASH_SILENT_COMMANDS:
            assert classify_command(cmd) == CommandSemantic.SILENT, f"{cmd} should be SILENT"

    def test_neutral_commands(self):
        for cmd in BASH_NEUTRAL_COMMANDS:
            assert classify_command(cmd) == CommandSemantic.NEUTRAL, f"{cmd} should be NEUTRAL"

    def test_unknown_commands(self):
        assert classify_command("npm") == CommandSemantic.UNKNOWN
        assert classify_command("git") == CommandSemantic.UNKNOWN
        assert classify_command("docker") == CommandSemantic.UNKNOWN

    def test_path_prefixed_command(self):
        assert classify_command("/usr/bin/grep") == CommandSemantic.SEARCH
        assert classify_command("/bin/ls") == CommandSemantic.LIST

    def test_command_with_args(self):
        assert classify_command("grep -r pattern") == CommandSemantic.SEARCH
        assert classify_command("ls -la /tmp") == CommandSemantic.LIST
        assert classify_command("cat file.txt") == CommandSemantic.READ

    def test_empty_command(self):
        assert classify_command("") == CommandSemantic.UNKNOWN
        assert classify_command("   ") == CommandSemantic.UNKNOWN


# --- classify_pipeline ---


class TestClassifyPipeline:
    def test_single_read_command(self):
        assert classify_pipeline("cat file.txt") == CommandSemantic.READ

    def test_single_search_command(self):
        assert classify_pipeline("grep pattern") == CommandSemantic.SEARCH

    def test_piped_read_commands(self):
        assert classify_pipeline("cat file.txt | sort | uniq") == CommandSemantic.READ

    def test_neutral_mixed_with_read(self):
        """Neutral commands (echo) don't affect classification."""
        assert classify_pipeline("ls dir && echo '---' && ls dir2") == CommandSemantic.LIST

    def test_destructive_mixed_with_read(self):
        """Non-neutral, non-collapsible command makes whole pipeline DESTRUCTIVE."""
        assert classify_pipeline("ls dir && rm file") == CommandSemantic.DESTRUCTIVE

    def test_pipe_search_to_read(self):
        """Mixed collapsible categories with search -> search wins."""
        result = classify_pipeline("grep pattern | wc -l")
        assert result == CommandSemantic.SEARCH

    def test_pipe_all_search(self):
        assert classify_pipeline("grep pattern | grep -v exclude") == CommandSemantic.SEARCH

    def test_all_neutral(self):
        """All neutral commands -> NEUTRAL."""
        assert classify_pipeline("echo hello && echo world") == CommandSemantic.NEUTRAL

    def test_silent_makes_destructive(self):
        """Silent commands (mv, cp) are not collapsible -> DESTRUCTIVE in pipeline."""
        assert classify_pipeline("ls dir && mv a b") == CommandSemantic.DESTRUCTIVE


# --- is_silent_command ---


class TestIsSilentCommand:
    def test_silent_commands(self):
        for cmd in BASH_SILENT_COMMANDS:
            assert is_silent_command(cmd), f"{cmd} should be silent"

    def test_non_silent_commands(self):
        assert not is_silent_command("ls")
        assert not is_silent_command("cat")
        assert not is_silent_command("grep")

    def test_silent_with_args(self):
        assert is_silent_command("mv a b")
        assert is_silent_command("mkdir -p dir")

    def test_empty_command(self):
        assert not is_silent_command("")
