"""Tests for tools/bash_rule_suggestion.py — smart permission rule suggestions."""

from gg_bond_code.tools.bash_rule_suggestion import (
    get_command_prefix,
    strip_safe_env_vars,
    SAFE_ENV_VARS,
    NEVER_SUGGEST_PREFIXES,
)


class TestGetCommandPrefix:
    def test_git_commit(self):
        assert get_command_prefix("git commit -m 'fix'") == "git commit"

    def test_npm_run(self):
        assert get_command_prefix("npm run build") == "npm run"

    def test_docker_build(self):
        assert get_command_prefix("docker build -t img .") == "docker build"

    def test_cargo_test(self):
        assert get_command_prefix("cargo test --release") == "cargo test"

    def test_safe_env_var(self):
        """Safe env vars are stripped before prefix extraction."""
        assert get_command_prefix("NODE_ENV=test npm run build") == "npm run"

    def test_unsafe_env_var(self):
        """Non-safe env vars -> no prefix suggestion."""
        assert get_command_prefix("PATH=/evil npm run build") is None

    def test_ld_preload_no_prefix(self):
        assert get_command_prefix("LD_PRELOAD=evil.so ls") is None

    def test_sudo_no_prefix(self):
        """sudo should never be suggested as a prefix."""
        assert get_command_prefix("sudo rm -rf /") is None

    def test_bash_no_prefix(self):
        """bash should never be suggested as a prefix."""
        assert get_command_prefix("bash -c 'echo hi'") is None

    def test_env_no_prefix(self):
        assert get_command_prefix("env PATH=/evil cmd") is None

    def test_single_command_no_prefix(self):
        """Single token (no subcommand) -> no prefix."""
        assert get_command_prefix("ls") is None

    def test_non_subcommand_token(self):
        """Second token that doesn't look like a subcommand returns None."""
        # Uppercase tokens don't match the subcommand pattern
        assert get_command_prefix("echo Hello world") is None
        # Numeric tokens don't match
        assert get_command_prefix("echo 123") is None

    def test_echo_hello_is_valid_prefix(self):
        """'hello' matches subcommand pattern — this is acceptable.
        Bash(echo hello:*) would auto-allow 'echo hello world', etc.
        """
        assert get_command_prefix("echo hello world") == "echo hello"

    def test_hyphenated_subcommand(self):
        """Hyphenated subcommands like 'run-dev' are valid."""
        assert get_command_prefix("npm run-dev --arg") == "npm run-dev"

    def test_empty_command(self):
        assert get_command_prefix("") is None

    def test_whitespace_command(self):
        assert get_command_prefix("   ") is None

    def test_go_subcommand(self):
        assert get_command_prefix("go test ./...") == "go test"


class TestStripSafeEnvVars:
    def test_strip_node_env(self):
        assert strip_safe_env_vars("NODE_ENV=test npm run build") == "npm run build"

    def test_strip_rust_backtrace(self):
        assert strip_safe_env_vars("RUST_BACKTRACE=1 cargo test") == "cargo test"

    def test_strip_multiple_safe_vars(self):
        assert strip_safe_env_vars("NODE_ENV=test RUST_BACKTRACE=1 npm run build") == "npm run build"

    def test_keep_unsafe_vars(self):
        """Unsafe env vars should NOT be stripped."""
        result = strip_safe_env_vars("PATH=/evil npm run build")
        assert "PATH=/evil" in result

    def test_no_env_vars(self):
        assert strip_safe_env_vars("npm run build") == "npm run build"

    def test_empty_command(self):
        assert strip_safe_env_vars("") == ""

    def test_only_safe_vars(self):
        """Only env var assignments -> empty command."""
        assert strip_safe_env_vars("NODE_ENV=test RUST_LOG=debug") == ""

    def test_pythonunbuffered(self):
        assert strip_safe_env_vars("PYTHONUNBUFFERED=1 python script.py") == "python script.py"
