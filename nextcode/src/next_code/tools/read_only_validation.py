"""Read-only command validation for BashTool.

Mirrors readOnlyValidation.ts — determines whether a bash command
is safe to auto-allow by checking against a whitelist of known-safe
commands and their safe flags.

Key design: FAIL-CLOSED — if a command or flag is not in the whitelist,
the command is NOT considered read-only. This means:
- Unknown commands -> need confirmation
- Known commands with unknown flags -> need confirmation
- Known commands with only safe flags -> auto-allowed

The whitelist approach is essential because blacklisting dangerous
flags is impossible — there are infinite ways to make a command
dangerous, but only a finite set of known-safe flags.
"""

from __future__ import annotations

import re
from enum import Enum


class FlagArgType(Enum):
    """Type of argument a flag expects."""
    NONE = "none"        # No argument (boolean flag like -l)
    NUMBER = "number"    # Numeric argument (like -n 10)
    STRING = "string"    # String argument (like -e "pattern")
    PATH = "path"        # Path argument (like -f /tmp/file)


class CommandConfig:
    """Configuration for a known-safe command."""

    __slots__ = ("safe_flags", "safe_subcommands", "regex", "respects_double_dash")

    def __init__(
        self,
        safe_flags: dict[str, FlagArgType] | None = None,
        safe_subcommands: set[str] | None = None,
        regex: re.Pattern | None = None,
        respects_double_dash: bool = True,
    ) -> None:
        self.safe_flags = safe_flags or {}
        # If set, the first positional arg must be one of these subcommands.
        # This prevents "git push" from being treated as read-only when
        # only "git status", "git log", etc. are safe.
        self.safe_subcommands = safe_subcommands
        self.regex = regex
        self.respects_double_dash = respects_double_dash


# ---------------------------------------------------------------------------
# Command configurations
# ---------------------------------------------------------------------------
# Each entry defines the flags that are safe (don't modify anything).
# Flags NOT listed are considered dangerous by default.
# SECURITY comments explain why certain flags are excluded.
# ---------------------------------------------------------------------------

SAFE_COMMANDS: dict[str, CommandConfig] = {
    # --- File listing/inspection ---
    "ls": CommandConfig(
        safe_flags={
            "-a": FlagArgType.NONE, "--all": FlagArgType.NONE,
            "-l": FlagArgType.NONE,
            "-h": FlagArgType.NONE, "--human-readable": FlagArgType.NONE,
            "-R": FlagArgType.NONE, "--recursive": FlagArgType.NONE,
            "-t": FlagArgType.NONE, "--sort=time": FlagArgType.NONE,
            "-S": FlagArgType.NONE, "--sort=size": FlagArgType.NONE,
            "-1": FlagArgType.NONE,
            "--color": FlagArgType.NONE,
            "--group-directories-first": FlagArgType.NONE,
            "-d": FlagArgType.NONE, "--directory": FlagArgType.NONE,
            "-p": FlagArgType.NONE, "--indicator-style": FlagArgType.NONE,
        },
    ),
    "cat": CommandConfig(
        safe_flags={
            "-n": FlagArgType.NONE, "--number": FlagArgType.NONE,
            "-b": FlagArgType.NONE, "--number-nonblank": FlagArgType.NONE,
            "-s": FlagArgType.NONE, "--squeeze-blank": FlagArgType.NONE,
            "-A": FlagArgType.NONE, "--show-all": FlagArgType.NONE,
            "-E": FlagArgType.NONE, "--show-ends": FlagArgType.NONE,
            "-T": FlagArgType.NONE, "--show-tabs": FlagArgType.NONE,
            "-v": FlagArgType.NONE, "--show-nonprinting": FlagArgType.NONE,
        },
    ),
    "head": CommandConfig(
        safe_flags={
            "-n": FlagArgType.NUMBER, "--lines": FlagArgType.NUMBER,
            "-c": FlagArgType.NUMBER, "--bytes": FlagArgType.NUMBER,
        },
    ),
    "tail": CommandConfig(
        safe_flags={
            "-n": FlagArgType.NUMBER, "--lines": FlagArgType.NUMBER,
            "-c": FlagArgType.NUMBER, "--bytes": FlagArgType.NUMBER,
            "-f": FlagArgType.NONE, "--follow": FlagArgType.NONE,
            "-F": FlagArgType.NONE,
        },
    ),
    "wc": CommandConfig(
        safe_flags={
            "-l": FlagArgType.NONE, "--lines": FlagArgType.NONE,
            "-w": FlagArgType.NONE, "--words": FlagArgType.NONE,
            "-c": FlagArgType.NONE, "--bytes": FlagArgType.NONE,
            "-m": FlagArgType.NONE, "--chars": FlagArgType.NONE,
            "-L": FlagArgType.NONE, "--max-line-length": FlagArgType.NONE,
        },
    ),
    "stat": CommandConfig(
        safe_flags={
            "-c": FlagArgType.STRING, "--format": FlagArgType.STRING,
            "-f": FlagArgType.NONE, "--file-system": FlagArgType.NONE,
            "-L": FlagArgType.NONE, "--dereference": FlagArgType.NONE,
        },
    ),
    "file": CommandConfig(
        safe_flags={
            "-b": FlagArgType.NONE, "--brief": FlagArgType.NONE,
            "-i": FlagArgType.NONE, "--mime-type": FlagArgType.NONE,
            "-L": FlagArgType.NONE, "--dereference": FlagArgType.NONE,
            "-h": FlagArgType.NONE, "--no-dereference": FlagArgType.NONE,
        },
    ),
    "strings": CommandConfig(
        safe_flags={
            "-n": FlagArgType.NUMBER,
            "-a": FlagArgType.NONE,
            "-t": FlagArgType.STRING,
        },
    ),

    # --- Search tools ---
    "find": CommandConfig(
        safe_flags={
            "-name": FlagArgType.STRING, "-iname": FlagArgType.STRING,
            "-type": FlagArgType.STRING, "-maxdepth": FlagArgType.NUMBER,
            "-mindepth": FlagArgType.NUMBER,
            "-path": FlagArgType.STRING, "-ipath": FlagArgType.STRING,
            "-size": FlagArgType.STRING, "-mtime": FlagArgType.STRING,
            "-atime": FlagArgType.STRING, "-ctime": FlagArgType.STRING,
            "-newer": FlagArgType.PATH,
            "-print": FlagArgType.NONE, "-print0": FlagArgType.NONE,
            "-not": FlagArgType.NONE, "-and": FlagArgType.NONE, "-or": FlagArgType.NONE,
            "-empty": FlagArgType.NONE,
            "-perm": FlagArgType.STRING,
            "-user": FlagArgType.STRING, "-group": FlagArgType.STRING,
            # SECURITY: -exec, -execdir, -ok, -delete are NOT in this list
            # They can modify files or execute arbitrary commands.
        },
    ),
    "grep": CommandConfig(
        safe_flags={
            "-i": FlagArgType.NONE, "--ignore-case": FlagArgType.NONE,
            "-r": FlagArgType.NONE, "-R": FlagArgType.NONE,
            "--recursive": FlagArgType.NONE,
            "-n": FlagArgType.NONE, "--line-number": FlagArgType.NONE,
            "-l": FlagArgType.NONE, "--files-with-matches": FlagArgType.NONE,
            "-L": FlagArgType.NONE, "--files-without-match": FlagArgType.NONE,
            "-c": FlagArgType.NONE, "--count": FlagArgType.NONE,
            "-v": FlagArgType.NONE, "--invert-match": FlagArgType.NONE,
            "-w": FlagArgType.NONE, "--word-regexp": FlagArgType.NONE,
            "-x": FlagArgType.NONE, "--line-regexp": FlagArgType.NONE,
            "-E": FlagArgType.NONE, "--extended-regexp": FlagArgType.NONE,
            "-F": FlagArgType.NONE, "--fixed-strings": FlagArgType.NONE,
            "-P": FlagArgType.NONE, "--perl-regexp": FlagArgType.NONE,
            "-e": FlagArgType.STRING, "--regexp": FlagArgType.STRING,
            "-f": FlagArgType.PATH, "--file": FlagArgType.PATH,
            "--include": FlagArgType.STRING, "--exclude": FlagArgType.STRING,
            "--exclude-dir": FlagArgType.STRING,
            "--color": FlagArgType.NONE,
            "-A": FlagArgType.NUMBER, "-B": FlagArgType.NUMBER,
            "-C": FlagArgType.NUMBER, "--context": FlagArgType.NUMBER,
        },
    ),
    "rg": CommandConfig(
        safe_flags={
            "-i": FlagArgType.NONE, "--ignore-case": FlagArgType.NONE,
            "-r": FlagArgType.NONE, "-R": FlagArgType.NONE,
            "-n": FlagArgType.NONE, "--line-number": FlagArgType.NONE,
            "-l": FlagArgType.NONE, "--files-with-matches": FlagArgType.NONE,
            "-c": FlagArgType.NONE, "--count": FlagArgType.NONE,
            "-v": FlagArgType.NONE, "--invert-match": FlagArgType.NONE,
            "-w": FlagArgType.NONE, "--word-regexp": FlagArgType.NONE,
            "-x": FlagArgType.NONE, "--line-regexp": FlagArgType.NONE,
            "-E": FlagArgType.NONE, "--extended-regexp": FlagArgType.NONE,
            "-F": FlagArgType.NONE, "--fixed-strings": FlagArgType.NONE,
            "-P": FlagArgType.NONE, "--pcre2": FlagArgType.NONE,
            "-e": FlagArgType.STRING, "--regexp": FlagArgType.STRING,
            "-f": FlagArgType.PATH, "--file": FlagArgType.PATH,
            "-g": FlagArgType.STRING, "--glob": FlagArgType.STRING,
            "--type": FlagArgType.STRING, "--type-add": FlagArgType.STRING,
            "--color": FlagArgType.NONE,
            "-A": FlagArgType.NUMBER, "-B": FlagArgType.NUMBER,
            "-C": FlagArgType.NUMBER, "--context": FlagArgType.NUMBER,
            "--max-count": FlagArgType.NUMBER,
            # SECURITY: rg -r (--replace) with --passthru can write to files
        },
    ),
    "ag": CommandConfig(
        safe_flags={
            "-i": FlagArgType.NONE, "--ignore-case": FlagArgType.NONE,
            "-l": FlagArgType.NONE, "--files-with-matches": FlagArgType.NONE,
            "-c": FlagArgType.NONE, "--count": FlagArgType.NONE,
            "-v": FlagArgType.NONE, "--invert-match": FlagArgType.NONE,
            "-w": FlagArgType.NONE, "--word-regexp": FlagArgType.NONE,
            "-Q": FlagArgType.NONE, "--literal": FlagArgType.NONE,
            "--color": FlagArgType.NONE,
            "-A": FlagArgType.NUMBER, "-B": FlagArgType.NUMBER,
            "-C": FlagArgType.NUMBER,
        },
    ),
    "fd": CommandConfig(
        safe_flags={
            "-h": FlagArgType.NONE, "--help": FlagArgType.NONE,
            "-H": FlagArgType.NONE, "--hidden": FlagArgType.NONE,
            "-i": FlagArgType.NONE, "--ignore-case": FlagArgType.NONE,
            "-d": FlagArgType.NUMBER, "--max-depth": FlagArgType.NUMBER,
            "-t": FlagArgType.STRING, "--type": FlagArgType.STRING,
            "-e": FlagArgType.STRING, "--extension": FlagArgType.STRING,
            "-g": FlagArgType.STRING, "--glob": FlagArgType.STRING,
            "--color": FlagArgType.NONE,
            "-L": FlagArgType.NONE, "--follow": FlagArgType.NONE,
            "-p": FlagArgType.NONE, "--full-path": FlagArgType.NONE,
            # SECURITY: -x/--exec and -X/--exec-batch are NOT in this list
            # They execute arbitrary commands on each search result.
            # SECURITY: -l/--list-details is NOT in this list
            # It internally executes ls, creating PATH hijack risk.
        },
    ),

    # --- Directory tools ---
    "tree": CommandConfig(
        safe_flags={
            "-L": FlagArgType.NUMBER,
            "-d": FlagArgType.NONE,
            "-a": FlagArgType.NONE,
            "-h": FlagArgType.NONE,
            "--dirsfirst": FlagArgType.NONE,
            "-p": FlagArgType.NONE,
            "-u": FlagArgType.NONE,
            "-g": FlagArgType.NONE,
            "-s": FlagArgType.NONE,
            "-D": FlagArgType.NONE,
        },
    ),
    "du": CommandConfig(
        safe_flags={
            "-h": FlagArgType.NONE, "--human-readable": FlagArgType.NONE,
            "-s": FlagArgType.NONE, "--summarize": FlagArgType.NONE,
            "-d": FlagArgType.NUMBER, "--max-depth": FlagArgType.NUMBER,
            "-a": FlagArgType.NONE, "--all": FlagArgType.NONE,
            "-c": FlagArgType.NONE, "--total": FlagArgType.NONE,
        },
    ),

    # --- Text processing (read-only usage) ---
    "jq": CommandConfig(
        safe_flags={
            "-r": FlagArgType.NONE, "--raw-output": FlagArgType.NONE,
            "-c": FlagArgType.NONE, "--compact-output": FlagArgType.NONE,
            "-e": FlagArgType.NONE, "--exit-status": FlagArgType.NONE,
            "-n": FlagArgType.NONE, "--null-input": FlagArgType.NONE,
            "-S": FlagArgType.NONE, "--sort-keys": FlagArgType.NONE,
            "-C": FlagArgType.NONE, "--color-output": FlagArgType.NONE,
            "-M": FlagArgType.NONE, "--monochrome-output": FlagArgType.NONE,
            "-j": FlagArgType.NONE, "--join-output": FlagArgType.NONE,
            "-f": FlagArgType.PATH, "--from-file": FlagArgType.PATH,
            # SECURITY: system/exec functions are blocked by bash_security.py
        },
    ),
    "sort": CommandConfig(
        safe_flags={
            "-r": FlagArgType.NONE, "--reverse": FlagArgType.NONE,
            "-n": FlagArgType.NONE, "--numeric-sort": FlagArgType.NONE,
            "-h": FlagArgType.NONE, "--human-numeric-sort": FlagArgType.NONE,
            "-k": FlagArgType.STRING, "--key": FlagArgType.STRING,
            "-t": FlagArgType.STRING, "--field-separator": FlagArgType.STRING,
            "-u": FlagArgType.NONE, "--unique": FlagArgType.NONE,
            "-f": FlagArgType.NONE, "--ignore-case": FlagArgType.NONE,
            "-R": FlagArgType.NONE, "--random-sort": FlagArgType.NONE,
            # SECURITY: -o/--output writes to a file
        },
    ),
    "uniq": CommandConfig(
        safe_flags={
            "-c": FlagArgType.NONE, "--count": FlagArgType.NONE,
            "-d": FlagArgType.NONE, "--repeated": FlagArgType.NONE,
            "-u": FlagArgType.NONE, "--unique": FlagArgType.NONE,
            "-i": FlagArgType.NONE, "--ignore-case": FlagArgType.NONE,
            "-f": FlagArgType.NUMBER, "--skip-fields": FlagArgType.NUMBER,
            "-s": FlagArgType.NUMBER, "--skip-chars": FlagArgType.NUMBER,
        },
    ),
    "cut": CommandConfig(
        safe_flags={
            "-d": FlagArgType.STRING, "--delimiter": FlagArgType.STRING,
            "-f": FlagArgType.STRING, "--fields": FlagArgType.STRING,
            "-c": FlagArgType.STRING, "--characters": FlagArgType.STRING,
            "-b": FlagArgType.STRING, "--bytes": FlagArgType.STRING,
            "-s": FlagArgType.NONE, "--only-delimited": FlagArgType.NONE,
        },
    ),
    "tr": CommandConfig(
        safe_flags={
            "-d": FlagArgType.NONE, "--delete": FlagArgType.NONE,
            "-s": FlagArgType.NONE, "--squeeze-repeats": FlagArgType.NONE,
            "-c": FlagArgType.NONE, "-C": FlagArgType.NONE, "--complement": FlagArgType.NONE,
            "-t": FlagArgType.NONE, "--truncate-set1": FlagArgType.NONE,
        },
    ),
    "awk": CommandConfig(
        safe_flags={
            "-F": FlagArgType.STRING,
            "-v": FlagArgType.STRING,
            "-f": FlagArgType.PATH,
            # SECURITY: awk can write files via print > and system()
            # These are caught by bash_security.py, but awk remains
            # risky. Only auto-allow simple invocations.
        },
    ),

    # --- System info ---
    "which": CommandConfig(safe_flags={"-a": FlagArgType.NONE}),
    "whereis": CommandConfig(safe_flags={}),
    "whoami": CommandConfig(safe_flags={}),
    "id": CommandConfig(safe_flags={}),
    "pwd": CommandConfig(safe_flags={"-L": FlagArgType.NONE, "-P": FlagArgType.NONE}),
    "date": CommandConfig(
        safe_flags={
            "-u": FlagArgType.NONE, "--utc": FlagArgType.NONE,
            "-R": FlagArgType.NONE, "--rfc-email": FlagArgType.NONE,
            "-I": FlagArgType.STRING, "--iso-8601": FlagArgType.STRING,
            "+": FlagArgType.STRING,
        },
    ),
    "uname": CommandConfig(
        safe_flags={
            "-a": FlagArgType.NONE, "-r": FlagArgType.NONE,
            "-m": FlagArgType.NONE, "-s": FlagArgType.NONE,
            "-n": FlagArgType.NONE, "-p": FlagArgType.NONE,
        },
    ),
    "hostname": CommandConfig(
        safe_flags={
            "-f": FlagArgType.NONE, "--fqdn": FlagArgType.NONE,
            "-i": FlagArgType.NONE, "--ip-address": FlagArgType.NONE,
            "-d": FlagArgType.NONE, "--domain": FlagArgType.NONE,
        },
    ),
    "df": CommandConfig(
        safe_flags={
            "-h": FlagArgType.NONE, "--human-readable": FlagArgType.NONE,
            "-i": FlagArgType.NONE, "--inodes": FlagArgType.NONE,
            "-T": FlagArgType.NONE, "--print-type": FlagArgType.NONE,
            "-t": FlagArgType.STRING, "--type": FlagArgType.STRING,
        },
    ),
    "free": CommandConfig(
        safe_flags={
            "-h": FlagArgType.NONE, "--human": FlagArgType.NONE,
            "-b": FlagArgType.NONE, "--bytes": FlagArgType.NONE,
            "-k": FlagArgType.NONE, "--kibi": FlagArgType.NONE,
            "-m": FlagArgType.NONE, "--mebi": FlagArgType.NONE,
            "-g": FlagArgType.NONE, "--gibi": FlagArgType.NONE,
        },
    ),
    "uptime": CommandConfig(safe_flags={}),
    "env": CommandConfig(safe_flags={}),  # Just "env" to print vars

    # --- Version checks ---
    "python": CommandConfig(
        safe_flags={
            "--version": FlagArgType.NONE, "-V": FlagArgType.NONE,
            "--help": FlagArgType.NONE, "-h": FlagArgType.NONE,
        },
        # SECURITY: python -c can execute arbitrary code.
        # Only auto-allow version/help checks.
    ),
    "python3": CommandConfig(
        safe_flags={
            "--version": FlagArgType.NONE, "-V": FlagArgType.NONE,
            "--help": FlagArgType.NONE, "-h": FlagArgType.NONE,
        },
    ),
    "node": CommandConfig(
        safe_flags={
            "--version": FlagArgType.NONE, "-v": FlagArgType.NONE,
            "--help": FlagArgType.NONE,
        },
    ),
    "go": CommandConfig(
        safe_subcommands={"version", "env", "list", "doc", "test", "vet"},
        safe_flags={},
    ),
    "cargo": CommandConfig(
        safe_flags={
            "--version": FlagArgType.NONE, "-V": FlagArgType.NONE,
            "--help": FlagArgType.NONE, "-h": FlagArgType.NONE,
        },
    ),
    "rustc": CommandConfig(
        safe_flags={
            "--version": FlagArgType.NONE, "-vV": FlagArgType.NONE,
            "--print": FlagArgType.STRING,
        },
    ),
    "java": CommandConfig(
        safe_flags={
            "-version": FlagArgType.NONE,
            "--version": FlagArgType.NONE,
            "-showversion": FlagArgType.NONE,
        },
    ),
    "ruby": CommandConfig(
        safe_flags={
            "--version": FlagArgType.NONE, "-v": FlagArgType.NONE,
        },
    ),

    # --- Package managers (read-only subcommands) ---
    "npm": CommandConfig(
        safe_subcommands={"list", "ls", "view", "info", "outdated", "run"},
        safe_flags={
            "--version": FlagArgType.NONE, "-v": FlagArgType.NONE,
        },
    ),
    "pip": CommandConfig(
        safe_subcommands={"list", "show", "search", "check"},
        safe_flags={
            "--version": FlagArgType.NONE,
        },
    ),
    "pip3": CommandConfig(
        safe_subcommands={"list", "show", "search", "check"},
        safe_flags={
            "--version": FlagArgType.NONE,
        },
    ),

    # --- Docker (read-only subcommands) ---
    "docker": CommandConfig(
        safe_subcommands={"ps", "images", "version", "info", "logs", "inspect", "compose"},
        safe_flags={},
    ),

    # --- Network (read-only) ---
    "curl": CommandConfig(
        safe_flags={
            "-s": FlagArgType.NONE, "--silent": FlagArgType.NONE,
            "-I": FlagArgType.NONE, "--head": FlagArgType.NONE,
            "-L": FlagArgType.NONE, "--location": FlagArgType.NONE,
            "-k": FlagArgType.NONE, "--insecure": FlagArgType.NONE,
            "-w": FlagArgType.STRING, "--write-out": FlagArgType.STRING,
            "--max-time": FlagArgType.NUMBER,
            "-o": FlagArgType.PATH, "--output": FlagArgType.PATH,
            "-D": FlagArgType.PATH, "--dump-header": FlagArgType.PATH,
            "-A": FlagArgType.STRING, "--user-agent": FlagArgType.STRING,
            "-H": FlagArgType.STRING, "--header": FlagArgType.STRING,
            "--url": FlagArgType.STRING,
            # SECURITY: -d/--data is NOT in this list (can send POST)
            # SECURITY: -X is NOT in this list (can change method to DELETE etc.)
        },
    ),
    "wget": CommandConfig(
        safe_flags={
            "--version": FlagArgType.NONE,
            "-q": FlagArgType.NONE, "--quiet": FlagArgType.NONE,
            "-S": FlagArgType.NONE, "--server-response": FlagArgType.NONE,
            "--spider": FlagArgType.NONE,
            # SECURITY: -O writes files, -P writes to directory
        },
    ),
    "ping": CommandConfig(
        safe_flags={
            "-c": FlagArgType.NUMBER, "--count": FlagArgType.NUMBER,
            "-W": FlagArgType.NUMBER, "--timeout": FlagArgType.NUMBER,
            "-i": FlagArgType.STRING,
        },
    ),
    "nslookup": CommandConfig(safe_flags={}),
    "dig": CommandConfig(safe_flags={"+short": FlagArgType.NONE}),
    "host": CommandConfig(safe_flags={}),
    "ssh-keygen": CommandConfig(
        safe_flags={
            "-l": FlagArgType.NONE, "-L": FlagArgType.NONE,  # fingerprint
            "-y": FlagArgType.NONE,  # read private -> print public
            "-f": FlagArgType.PATH,
        },
    ),

    # --- Git (read-only subcommands ONLY) ---
    # SECURITY: Only subcommands that read/display info are safe.
    # Write subcommands (commit, push, merge, rebase, reset, checkout with
    # paths, clean, cherry-pick, etc.) are NOT safe and NOT listed here.
    "git": CommandConfig(
        safe_subcommands={
            "status", "log", "diff", "show", "branch", "tag", "remote",
            "stash", "describe", "rev-parse", "reflog", "shortlog", "blame",
            "ls-files", "ls-tree", "ls-remote", "config", "worktree", "submodule",
        },
        safe_flags={
            # Common flags
            "--oneline": FlagArgType.NONE,
            "--stat": FlagArgType.NONE,
            "--short": FlagArgType.NONE,
            "--porcelain": FlagArgType.NONE,
            "-n": FlagArgType.NUMBER,
            "--no-color": FlagArgType.NONE,
            "--color": FlagArgType.NONE,
        },
    ),

    # --- Echo (neutral command, usually safe) ---
    "echo": CommandConfig(
        safe_flags={
            "-n": FlagArgType.NONE, "-e": FlagArgType.NONE, "-E": FlagArgType.NONE,
        },
    ),
    "printf": CommandConfig(safe_flags={}),
    "true": CommandConfig(safe_flags={}),
    "false": CommandConfig(safe_flags={}),
    ":": CommandConfig(safe_flags={}),

    # --- Misc safe commands ---
    "seq": CommandConfig(
        safe_flags={
            "-s": FlagArgType.STRING, "--separator": FlagArgType.STRING,
            "-w": FlagArgType.NONE, "--equal-width": FlagArgType.NONE,
            "-f": FlagArgType.STRING, "--format": FlagArgType.STRING,
        },
    ),
    "tee": CommandConfig(
        safe_flags={
            "-a": FlagArgType.NONE, "--append": FlagArgType.NONE,
            # SECURITY: tee writes to files. Only allow with -a (append)
            # since the primary use is piping through + capturing.
            # Actually, tee always writes. Don't auto-allow.
        },
    ),
}


def _extract_base_and_args(command: str) -> tuple[str, list[str]]:
    """Extract base command and arguments from a command string.

    Handles:
    - Leading environment variables (KEY=value ...)
    - Path-prefixed commands (/usr/bin/git -> git)
    - Safe wrapper stripping (nice, timeout, time, ...)
    """
    from .bash_security import BARE_SHELL_PREFIXES

    tokens = command.strip().split()
    if not tokens:
        return "", []

    # Skip environment variable assignments (only safe ones)
    from .bash_rule_suggestion import SAFE_ENV_VARS, _ENV_VAR_RE
    i = 0
    while i < len(tokens) and _ENV_VAR_RE.match(tokens[i]):
        var_name = tokens[i].split("=")[0]
        if var_name not in SAFE_ENV_VARS:
            # Non-safe env var -> stop stripping, don't auto-allow
            return "", []
        i += 1

    if i >= len(tokens):
        return "", []

    # Strip safe wrappers iteratively
    base = tokens[i].rsplit("/", 1)[-1] if "/" in tokens[i] else tokens[i]
    while base in BARE_SHELL_PREFIXES and i + 1 < len(tokens):
        i += 1
        # Some wrappers take a numeric argument (timeout 30, nice 10)
        if base in ("timeout", "nice", "stdbuf", "nohup", "time"):
            if i < len(tokens) and tokens[i].lstrip("-").replace(".", "").isdigit():
                i += 1
        if i >= len(tokens):
            return "", []
        base = tokens[i].rsplit("/", 1)[-1] if "/" in tokens[i] else tokens[i]

    args = tokens[i + 1:]
    return base, args


def is_read_only_command(command: str) -> bool:
    """Check if a command is read-only based on the whitelist.

    FAIL-CLOSED: if the command or any of its flags are not in the
    whitelist, returns False.

    Args:
        command: The bash command to check.

    Returns:
        True if the command is considered safe/read-only.
    """
    # First, run security checks
    from .bash_security import analyze_command_security
    security_result = analyze_command_security(command)
    if not security_result.is_safe:
        return False

    # Extract base command and arguments
    base, args = _extract_base_and_args(command)
    if not base:
        return False

    # Look up command config
    config = SAFE_COMMANDS.get(base)
    if config is None:
        # Unknown command — not in whitelist -> fail-closed
        return False

    # For subcommand-style commands, the first positional arg must be
    # a known-safe subcommand. This prevents "git push" from being
    # considered read-only when only "git status" etc. are safe.
    if config.safe_subcommands is not None and args:
        subcmd = args[0]
        if subcmd not in config.safe_subcommands:
            return False

    # Check all flags against safe list
    i = 0
    while i < len(args):
        arg = args[i]

        # Positional arguments (paths, patterns, values) are generally safe
        if not arg.startswith("-"):
            i += 1
            continue

        # Double dash separator — everything after is positional
        if arg == "--":
            break

        # Check if this flag is in the safe list
        if arg in config.safe_flags:
            flag_type = config.safe_flags[arg]
            if flag_type != FlagArgType.NONE:
                i += 1  # Skip the argument value
            i += 1
            continue

        # Combined short flags (e.g., -la)
        if len(arg) > 2 and arg[0] == "-" and arg[1] != "-":
            # Check each character
            safe_short_flags = {
                f.lstrip("-") for f in config.safe_flags if len(f) == 2 and f.startswith("-")
            }
            all_safe = all(c in safe_short_flags for c in arg[1:] if c.isalpha())
            if all_safe:
                i += 1
                continue

        # Unknown flag — fail-closed
        return False

    return True


# Regex for splitting compound commands
_COMPOUND_SPLIT_RE = re.compile(r"\s*(?:&&|\|\|)\s*")
_PIPE_SPLIT_RE = re.compile(r"\s*\|\s*")


def check_read_only_constraints(command: str) -> bool:
    """Check if a full command (possibly with pipes/&&) is read-only.

    All sub-commands must be individually read-only for the whole
    command to be considered read-only.
    """
    # Split on pipe operators
    parts = _PIPE_SPLIT_RE.split(command)

    for part in parts:
        # Further split on && and ||
        subcmds = _COMPOUND_SPLIT_RE.split(part)
        for subcmd in subcmds:
            subcmd = subcmd.strip()
            if not subcmd:
                continue
            if not is_read_only_command(subcmd):
                return False

    return True
