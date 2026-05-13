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


def get_command_prefix(command: str) -> str | None:
    """Extract a reusable prefix from a command for permission rule suggestion.

    The prefix consists of the base command + first subcommand/token,
    stripped of safe environment variable assignments and safe wrappers.

    Examples:
        "git commit -m 'fix'" -> "git commit"
        "NODE_ENV=test npm run build" -> "npm run"
        "MY_VAR=val npm run build" -> None (MY_VAR not in safe vars)
        "sudo rm -rf /" -> None (sudo is never-suggest)
        "ls" -> None (single token, no subcommand)

    Args:
        command: The command to extract a prefix from.

    Returns:
        The suggested prefix string, or None if no good prefix can be extracted.
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
