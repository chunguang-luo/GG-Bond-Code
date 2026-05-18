"""Input validation for BashTool.

Mirrors validateInput() from BashTool.tsx — detects patterns
that should be handled differently (e.g., sleep-based polling
should use run_in_background instead of blocking the session).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Result of input validation."""
    is_valid: bool = True
    message: str = ""
    error_code: int = 0


# Pattern: sleep N at the beginning of a command (N >= 2)
_SLEEP_PATTERN = re.compile(r"^\s*sleep\s+(\d+)")


def detect_blocked_sleep_pattern(command: str) -> str | None:
    """Detect sleep-based polling patterns.

    Sleep < 2 seconds is allowed (used for rate limiting).
    Sleep >= 2 seconds should use run_in_background instead.

    Returns the matched pattern string if detected, None otherwise.
    """
    match = _SLEEP_PATTERN.match(command)
    if match and int(match.group(1)) >= 2:
        return f"sleep {match.group(1)}"
    return None


def validate_bash_input(
    command: str,
    *,
    run_in_background: bool = False,
) -> ValidationResult:
    """Validate BashTool input before execution.

    Args:
        command: The command to validate.
        run_in_background: Whether the command will run in background.

    Returns:
        ValidationResult with is_valid=False if the command should
        be blocked or redirected.
    """
    if not command.strip():
        return ValidationResult(is_valid=False, message="Empty command", error_code=1)

    # Check for sleep-based polling
    sleep_pattern = detect_blocked_sleep_pattern(command)
    if sleep_pattern is not None and not run_in_background:
        return ValidationResult(
            is_valid=False,
            message=(
                f"Blocked: {sleep_pattern}. Run blocking commands in the background "
                "using run_in_background: true"
            ),
            error_code=10,
        )

    return ValidationResult(is_valid=True)
