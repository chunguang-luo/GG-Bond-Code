"""Exit code semantic interpretation for BashTool.

Mirrors commandSemantics.ts — many commands use non-zero exit codes
to convey information rather than errors. For example, grep returns 1
when no matches are found, not because of an actual error.

Without this system, the AI would see `grep` returning exit code 1
and assume the command failed, then attempt to "fix" a non-existent
problem — wasting turns and potentially introducing real errors.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExitCodeSemantic:
    """Semantic interpretation of an exit code."""
    is_error: bool
    message: str | None = None


def _grep_semantic(exit_code: int) -> ExitCodeSemantic:
    """grep/rg/ag: 0=found, 1=not found, 2+=error."""
    if exit_code == 0:
        return ExitCodeSemantic(is_error=False)
    if exit_code == 1:
        return ExitCodeSemantic(is_error=False, message="No matches found")
    return ExitCodeSemantic(is_error=True)


def _diff_semantic(exit_code: int) -> ExitCodeSemantic:
    """diff: 0=same, 1=different, 2+=error."""
    if exit_code == 0:
        return ExitCodeSemantic(is_error=False)
    if exit_code == 1:
        return ExitCodeSemantic(is_error=False, message="Files differ")
    return ExitCodeSemantic(is_error=True)


def _test_semantic(exit_code: int) -> ExitCodeSemantic:
    """test/[: 0=true, 1=false, 2+=error."""
    if exit_code <= 1:
        return ExitCodeSemantic(
            is_error=False,
            message="Condition is false" if exit_code == 1 else None,
        )
    return ExitCodeSemantic(is_error=True)


def _git_semantic(exit_code: int) -> ExitCodeSemantic:
    """git: many subcommands return 1 for expected conditions.

    e.g. `git diff` with changes returns 1, `git merge --no-commit`
    returns 1 to indicate merge stopped. These are not errors.
    """
    if exit_code == 0:
        return ExitCodeSemantic(is_error=False)
    if exit_code == 1:
        return ExitCodeSemantic(is_error=False, message="Command returned 1 (expected condition)")
    return ExitCodeSemantic(is_error=True)


def _curl_semantic(exit_code: int) -> ExitCodeSemantic:
    """curl: many non-zero codes are informational (e.g. 22=HTTP error, 28=timeout).

    For now treat all non-zero as error since curl errors are usually real.
    """
    return ExitCodeSemantic(is_error=exit_code != 0)


def _default_semantic(exit_code: int) -> ExitCodeSemantic:
    """Default: non-zero = error."""
    return ExitCodeSemantic(is_error=exit_code != 0)


# Registry of command-specific exit code interpreters
# Key: base command name, Value: interpreter function
COMMAND_SEMANTICS: dict[str, __import__("typing").Callable[[int], ExitCodeSemantic]] = {
    "grep": _grep_semantic,
    "egrep": _grep_semantic,
    "fgrep": _grep_semantic,
    "rg": _grep_semantic,   # ripgrep follows same convention
    "ag": _grep_semantic,   # silver searcher follows same convention
    "ack": _grep_semantic,
    "diff": _diff_semantic,
    "diff3": _diff_semantic,
    "test": _test_semantic,
    "[": _test_semantic,
    "[[": _test_semantic,
    "git": _git_semantic,
    "curl": _curl_semantic,
}


def interpret_exit_code(command: str, exit_code: int) -> ExitCodeSemantic:
    """Interpret the exit code of a command semantically.

    Args:
        command: The command that was executed.
        exit_code: The exit code returned.

    Returns:
        ExitCodeSemantic with is_error flag and optional message.
    """
    # Extract base command
    stripped = command.strip()
    if not stripped:
        return _default_semantic(exit_code)

    base = stripped.split()[0]
    if "/" in base:
        base = base.rsplit("/", maxsplit=1)[1]

    interpreter = COMMAND_SEMANTICS.get(base, _default_semantic)
    return interpreter(exit_code)
