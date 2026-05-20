"""Smart permission rule suggestion for BashTool.

Mirrors getSimpleCommandPrefix() and suggestionForExactCommand()
from bashPermissions.ts — when the user approves a command,
automatically suggest a reusable permission rule prefix.

Example flow:
  User approves: "git commit -m 'fix typo'"
  System suggests: Bash(git commit:*) — allows all git commit commands

Key security considerations:
- Never suggest prefixes for bare shells (bash, sh, zsh, etc.)
  because Bash(bash:*) == Bash(*)
- Never suggest prefixes for sudo/env because Bash(sudo:*) == Bash(*)
- Only strip safe environment variables from prefix extraction
  because PATH=evil npm run build should NOT become "npm run"
"""

from __future__ import annotations

import re


# Safe environment variables that can be stripped from command prefix.
# These only change behavior configuration — they can't execute code.
SAFE_ENV_VARS: frozenset[str] = frozenset({
    # Go
    "GOEXPERIMENT", "GOOS", "GOARCH", "CGO_ENABLED", "GO111MODULE",
    # Rust
    "RUST_BACKTRACE", "RUST_LOG",
    # Node
    "NODE_ENV",
    # Python
    "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    # Java
    "JAVA_HOME",
    # Rust
    "CARGO_HOME",
    # SECURITY: The following variables must NEVER be added to this list:
    # PATH, LD_PRELOAD, LD_LIBRARY_PATH, DYLD_* — can hijack binaries
    # PYTHONPATH, NODE_PATH — can inject modules
    # GOFLAGS, RUSTFLAGS, NODE_OPTIONS — can inject code execution flags
})

# Prefixes that should never be suggested as permission rule prefixes.
# Bash(bash:*) == Bash(*), Bash(sudo:*) == Bash(*), etc.
NEVER_SUGGEST_PREFIXES: frozenset[str] = frozenset({
    "sh", "bash", "zsh", "fish", "csh", "ksh", "dash",
    "env", "xargs",
    "nice", "stdbuf", "nohup", "timeout", "time",
    "sudo", "doas", "pkexec",
    "eval", "exec", "source",
})

# Regex for a "subcommand-like" token: starts with lowercase letter,
# contains only lowercase alphanumeric and hyphens.
_SUBCMD_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# Regex for environment variable assignment (KEY=value)
_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Command separators that create compound commands
_COMPOUND_SEPARATORS = re.compile(r"&&|\|\||;")

# Commands that are just "setup" in compound commands — safe to strip for
# permission key matching. They don't change what the main command does
# in a security-relevant way.
_SETUP_COMMANDS: frozenset[str] = frozenset({
    "cd", "source", ".",  # cd and source are pure setup
    "pushd", "popd", "dirs",  # directory stack
    "export", "set", "unset", "alias", "unalias",  # shell config
})


def _split_compound(command: str) -> list[str]:
    """Split a compound command into individual simple commands.

    Handles &&, ||, and ; separators. Preserves order so callers
    can pick the most meaningful segment (typically the last non-trivial one).

    Examples:
        "cd /tmp && npm run build" -> ["cd /tmp", "npm run build"]
        "git add . && git commit -m 'fix'" -> ["git add .", "git commit -m 'fix'"]
        "ls" -> ["ls"]
    """
    parts = _COMPOUND_SEPARATORS.split(command)
    return [p.strip() for p in parts if p.strip()]


def _extract_prefix_single(command: str) -> str | None:
    """Extract a prefix from a single (non-compound) command.

    Returns the "base subcommand" (e.g. "git commit") or None.
    """
    tokens = command.strip().split()
    if not tokens:
        return None

    # Skip safe environment variable assignments
    i = 0
    while i < len(tokens) and _ENV_VAR_RE.match(tokens[i]):
        var_name = tokens[i].split("=")[0]
        if var_name not in SAFE_ENV_VARS:
            # Non-safe env var -> don't suggest prefix
            return None
        i += 1

    if i >= len(tokens):
        return None

    # Get base command (strip path prefix)
    remaining = tokens[i:]
    base = remaining[0].rsplit("/", 1)[-1] if "/" in remaining[0] else remaining[0]

    # Never suggest for bare shells and dangerous prefixes
    if base in NEVER_SUGGEST_PREFIXES:
        return None

    # Need at least 2 tokens (command + subcommand)
    if len(remaining) < 2:
        return None

    # Second token must look like a subcommand
    subcmd = remaining[1]
    if not _SUBCMD_RE.match(subcmd):
        return None

    return " ".join(remaining[:2])


def get_command_prefix(command: str) -> str | None:
    """Extract a reusable prefix from a command for permission rule suggestion.

    For compound commands (&&, ||, ;), iterates from last segment to first
    and returns the prefix of the first segment that yields a meaningful result.
    This handles patterns like "cd /dir && npm run build" where the meaningful
    command is "npm run", not "cd".

    Examples:
        "git commit -m 'fix'" -> "git commit"
        "cd /tmp && npm run build" -> "npm run"
        "NODE_ENV=test npm run build" -> "npm run"
        "sudo rm -rf /" -> None (sudo is never-suggest)
        "ls" -> None (single token, no subcommand)

    Args:
        command: The command to extract a prefix from.

    Returns:
        The suggested prefix string, or None if no good prefix can be extracted.
    """
    segments = _split_compound(command)

    # For compound commands, try from the last segment backwards
    # (the last segment is typically the main action, earlier ones are setup)
    for segment in reversed(segments):
        prefix = _extract_prefix_single(segment)
        if prefix is not None:
            return prefix

    # All segments returned None — try the last segment's base command
    if segments:
        return _extract_base_command(segments[-1])

    return None


def _extract_base_command(command: str) -> str | None:
    """Extract just the base command name from a simple command.

    Used as a fallback when no subcommand prefix can be extracted.
    Returns the command name (e.g. "ls", "python3") or None for dangerous commands.

    Only returns a result for single-token commands; multi-token commands
    without valid subcommands should return None.
    """
    tokens = command.strip().split()
    if not tokens:
        return None

    # Skip safe env vars
    i = 0
    while i < len(tokens) and _ENV_VAR_RE.match(tokens[i]):
        var_name = tokens[i].split("=")[0]
        if var_name not in SAFE_ENV_VARS:
            return None
        i += 1

    if i >= len(tokens):
        return None

    base = tokens[i].rsplit("/", 1)[-1] if "/" in tokens[i] else tokens[i]

    if base in NEVER_SUGGEST_PREFIXES:
        return None

    # Only return base command for single-token commands without subcommands.
    # Multi-token commands without valid subcommands should return None.
    if len(tokens) - i == 1:
        return None

    return base


def strip_safe_env_vars(command: str) -> str:
    """Strip leading safe environment variable assignments from a command.

    Used for permission key generation — we want `NODE_ENV=test npm run build`
    to match the rule for `npm run` rather than requiring a separate rule
    for each env var combination.

    Args:
        command: The command to strip env vars from.

    Returns:
        The command with safe env var assignments removed.
    """
    tokens = command.strip().split()
    if not tokens:
        return command

    i = 0
    while i < len(tokens) and _ENV_VAR_RE.match(tokens[i]):
        var_name = tokens[i].split("=")[0]
        if var_name not in SAFE_ENV_VARS:
            break
        i += 1

    return " ".join(tokens[i:])


def strip_compound_prefix(command: str) -> str:
    """Strip setup command prefixes from a compound command for permission matching.

    Handles patterns like `cd /dir && source venv && pytest tests/` by stripping
    the `cd` and `source` segments so the resulting key starts with the main
    command (`pytest tests/`), matching rules like `Bash:pytest:*`.

    Only strips setup commands (cd, source, export, etc.) — never strips
    actual commands like `npm`, `git`, `pytest`.

    Args:
        command: The command to strip setup prefixes from.

    Returns:
        The command with setup prefixes removed.
    """
    segments = _split_compound(command)

    # Find the first non-setup segment
    for i, segment in enumerate(segments):
        tokens = segment.strip().split()
        if not tokens:
            continue
        base = tokens[0].rsplit("/", 1)[-1] if "/" in tokens[0] else tokens[0]
        # Skip safe env var prefixes within the segment
        j = 0
        while j < len(tokens) and _ENV_VAR_RE.match(tokens[j]):
            j += 1
        if j < len(tokens):
            seg_base = tokens[j].rsplit("/", 1)[-1] if "/" in tokens[j] else tokens[j]
        else:
            seg_base = base
        if seg_base not in _SETUP_COMMANDS:
            # This is the main command — return from here onwards
            return " && ".join(segments[i:])

    # All segments are setup — return original
    return command
