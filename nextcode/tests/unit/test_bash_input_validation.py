"""Tests for tools/bash_input_validation.py — input validation."""

from next_code.tools.bash_input_validation import (
    validate_bash_input,
    detect_blocked_sleep_pattern,
)


class TestDetectBlockedSleepPattern:
    def test_sleep_5(self):
        assert detect_blocked_sleep_pattern("sleep 5") == "sleep 5"

    def test_sleep_1(self):
        """Sleep < 2 is allowed (rate limiting)."""
        assert detect_blocked_sleep_pattern("sleep 1") is None

    def test_sleep_0(self):
        assert detect_blocked_sleep_pattern("sleep 0") is None

    def test_sleep_with_leading_whitespace(self):
        assert detect_blocked_sleep_pattern("  sleep 5") == "sleep 5"

    def test_sleep_not_at_start(self):
        """sleep not at the beginning is allowed."""
        assert detect_blocked_sleep_pattern("echo hello && sleep 5") is None

    def test_no_sleep(self):
        assert detect_blocked_sleep_pattern("ls -la") is None


class TestValidateBashInput:
    def test_empty_command(self):
        result = validate_bash_input("")
        assert not result.is_valid
        assert result.error_code == 1

    def test_whitespace_command(self):
        result = validate_bash_input("   ")
        assert not result.is_valid

    def test_sleep_blocked(self):
        result = validate_bash_input("sleep 5")
        assert not result.is_valid
        assert result.error_code == 10
        assert "sleep 5" in result.message

    def test_sleep_allowed_in_background(self):
        result = validate_bash_input("sleep 5", run_in_background=True)
        assert result.is_valid

    def test_normal_command(self):
        result = validate_bash_input("ls -la")
        assert result.is_valid

    def test_sleep_1_allowed(self):
        result = validate_bash_input("sleep 1")
        assert result.is_valid
