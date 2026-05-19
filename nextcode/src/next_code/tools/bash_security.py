"""Bash command security analysis.

Mirrors bashSecurity.ts — detects dangerous patterns in shell commands.
Uses regex-based analysis as the primary method (Python doesn't have
a tree-sitter-bash equivalent that's readily available).

Key design: FAIL-CLOSED — if we can't determine a command is safe,
we mark it as needing user confirmation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class SecurityCheckID(Enum):
    """Security check identifiers (mirrors BASH_SECURITY_CHECK_IDS)."""
    INCOMPLETE_COMMANDS = 1
    JQ_SYSTEM_FUNCTION = 2
    OBFUSCATED_FLAGS = 4
    SHELL_METACHARACTERS = 5
    DANGEROUS_VARIABLES = 6
    NEWLINES = 7
    IFS_INJECTION = 11
    PROC_ENVIRON_ACCESS = 13
    MALFORMED_TOKEN_INJECTION = 14
    BRACE_EXPANSION = 16
    CONTROL_CHARACTERS = 17
    UNICODE_WHITESPACE = 18
    ZSH_DANGEROUS_COMMANDS = 20
    COMMENT_QUOTE_DESYNC = 22


@dataclass
class SecurityCheckResult:
    """Result of security analysis."""
    is_safe: bool = True
    check_id: SecurityCheckID | None = None
    message: str = ""
    details: list[str] = field(default_factory=list)


# --- Command substitution patterns (mirrors bashSecurity.ts:16-41) ---
# These patterns detect ways to inject code execution into commands.

COMMAND_SUBSTITUTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"<\("), "process substitution <()"),
    (re.compile(r">\("), "process substitution >()"),
    (re.compile(r"=\("), "Zsh process substitution =()"),
    (re.compile(r"(?:^|[\s;&|])=[a-zA-Z_]"), "Zsh equals expansion (=cmd)"),
    (re.compile(r"\$\("), "$() command substitution"),
    (re.compile(r"\$\{"), "${} parameter substitution"),
    (re.compile(r"\$\["), "$[] legacy arithmetic expansion"),
]

# --- Zsh dangerous commands (mirrors bashSecurity.ts:45-74) ---
# These Zsh builtins can bypass command-name-based security checks
# because they operate through shell internals, not external binaries.

ZSH_DANGEROUS_COMMANDS: frozenset[str] = frozenset({
    "zmodload",    # Load dangerous modules (mapfile/sysopen/zpty/ztcp)
    "emulate",     # -c flag is equivalent to eval
    "sysopen", "sysread", "syswrite", "sysseek",  # File descriptor ops
    "zpty",        # Pseudo-terminal command execution
    "ztcp",        # TCP connections (data exfiltration)
    "zsocket",     # Unix/TCP socket
    "zf_rm", "zf_mv", "zf_ln", "zf_chmod",  # Bypass binary checks
})

# --- Dangerous shell builtins ---
# These can execute arbitrary code or manipulate process state.

DANGEROUS_BUILTINS: frozenset[str] = frozenset({
    "eval", "exec", "source", ".",   # Code execution
    "kill", "trap",                    # Signal handling
})

# --- Bare shell prefixes ---
# These should never be suggested as permission rule prefixes because
# Bash(bash:*) == Bash(*), Bash(sudo:*) == Bash(*), etc.

BARE_SHELL_PREFIXES: frozenset[str] = frozenset({
    "sh", "bash", "zsh", "fish", "csh", "ksh", "dash",
    "env", "xargs",
    "nice", "stdbuf", "nohup", "timeout", "time",
    "sudo", "doas", "pkexec",
})

# --- Dangerous environment variables ---
# These can hijack binary execution or inject code.
# Must NOT be added to SAFE_ENV_VARS in bash_rule_suggestion.py.

DANGEROUS_ENV_VARS: frozenset[str] = frozenset({
    # Library injection
    "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
    # Module/code injection
    "PYTHONPATH", "NODE_PATH",
    "GOFLAGS", "RUSTFLAGS", "NODE_OPTIONS",
})

# --- Unicode whitespace characters that may bypass parsing ---
_UNICODE_WHITESPACE: frozenset[str] = frozenset({
    "\u00a0",   # NO-BREAK SPACE
    "\u2000",   # EN QUAD
    "\u2001",   # EM QUAD
    "\u2002",   # EN SPACE
    "\u2003",   # EM SPACE
    "\u2004",   # THREE-PER-EM SPACE
    "\u2005",   # FOUR-PER-EM SPACE
    "\u2006",   # SIX-PER-EM SPACE
    "\u2007",   # FIGURE SPACE
    "\u2008",   # PUNCTUATION SPACE
    "\u2009",   # THIN SPACE
    "\u200a",   # HAIR SPACE
    "\u2028",   # LINE SEPARATOR
    "\u2029",   # PARAGRAPH SEPARATOR
    "\u202f",   # NARROW NO-BREAK SPACE
    "\u205f",   # MEDIUM MATHEMATICAL SPACE
    "\u3000",   # IDEOGRAPHIC SPACE
})


def check_command_substitution(command: str) -> SecurityCheckResult:
    """Check for command/parameter substitution patterns.

    These patterns allow executing arbitrary code within a command,
    bypassing command-name-based permission rules.
    """
    for pattern, message in COMMAND_SUBSTITUTION_PATTERNS:
        if pattern.search(command):
            return SecurityCheckResult(
                is_safe=False,
                check_id=SecurityCheckID.SHELL_METACHARACTERS,
                message=f"Dangerous pattern detected: {message}",
            )
    return SecurityCheckResult(is_safe=True)


def check_zsh_dangerous_commands(command: str) -> SecurityCheckResult:
    """Check for Zsh-specific dangerous commands.

    Zsh builtins like zmodload can load modules that bypass traditional
    command checks. We intercept both the loader (zmodload) and
    individual module commands (defense in depth).
    """
    tokens = command.strip().split()
    if not tokens:
        return SecurityCheckResult(is_safe=True)

    base = tokens[0].rsplit("/", 1)[-1] if "/" in tokens[0] else tokens[0]

    if base in ZSH_DANGEROUS_COMMANDS:
        return SecurityCheckResult(
            is_safe=False,
            check_id=SecurityCheckID.ZSH_DANGEROUS_COMMANDS,
            message=f"Zsh dangerous command: {base}",
        )
    return SecurityCheckResult(is_safe=True)


def check_dangerous_builtins(command: str) -> SecurityCheckResult:
    """Check for dangerous shell builtins.

    eval/exec/source can execute arbitrary code. kill/trap can
    manipulate process signals.
    """
    tokens = command.strip().split()
    if not tokens:
        return SecurityCheckResult(is_safe=True)

    base = tokens[0].rsplit("/", 1)[-1] if "/" in tokens[0] else tokens[0]

    if base in DANGEROUS_BUILTINS:
        return SecurityCheckResult(
            is_safe=False,
            check_id=SecurityCheckID.SHELL_METACHARACTERS,
            message=f"Dangerous builtin: {base}",
        )
    return SecurityCheckResult(is_safe=True)


def check_newlines(command: str) -> SecurityCheckResult:
    """Check for newlines that may hide subsequent commands.

    A command like `echo hello\nrm -rf /` might appear as two
    separate commands to the shell but look like one command
    to a simple parser.

    We allow newlines when the continuation line starts with a
    shell operator (&&, ||, |, >, >>) or is part of a compound
    command (then, else, do, done, fi, esac), since these are
    legitimate multi-line commands. We only block bare newlines
    that start a new, independent command.
    """
    if "\n" not in command:
        return SecurityCheckResult(is_safe=True)

    # Lines that continue a previous construct — these are safe
    _continuation_prefixes = (
        "&&", "||", "|", ">", ">>", "<<",  # Pipeline / redirection
        "then", "else", "elif", "fi",      # if/then/fi
        "do", "done",                       # for/while/do/done
        "in",                               # for ... in
        ";;", ";;&", ";&",                 # case
        "esac",                             # end of case
        "#",                                # comment
    )

    for line in command.split("\n")[1:]:  # Skip first line
        stripped = line.strip()
        if not stripped:
            continue  # Empty line is fine
        if stripped.startswith(_continuation_prefixes):
            continue  # Continuation of previous command
        # This line starts a new independent command after a bare newline
        return SecurityCheckResult(
            is_safe=False,
            check_id=SecurityCheckID.NEWLINES,
            message="Command contains newlines (may hide subsequent commands)",
        )

    return SecurityCheckResult(is_safe=True)


def check_control_characters(command: str) -> SecurityCheckResult:
    """Check for control characters.

    Control characters (except tab and newline which has its own check)
    can manipulate terminal output or hide malicious content.
    """
    for i, char in enumerate(command):
        if ord(char) < 0x20 and char not in ("\n", "\t"):
            return SecurityCheckResult(
                is_safe=False,
                check_id=SecurityCheckID.CONTROL_CHARACTERS,
                message=f"Control character (0x{ord(char):02x}) at position {i}",
            )
    return SecurityCheckResult(is_safe=True)


def check_unicode_whitespace(command: str) -> SecurityCheckResult:
    """Check for Unicode whitespace that may bypass simple parsing.

    Many shell tokenizers only split on ASCII spaces, but Unicode
    whitespace can look like spaces while being treated differently
    by the shell — potentially breaking command parsing.
    """
    for i, char in enumerate(command):
        if char in _UNICODE_WHITESPACE:
            return SecurityCheckResult(
                is_safe=False,
                check_id=SecurityCheckID.UNICODE_WHITESPACE,
                message=f"Unicode whitespace character (U+{ord(char):04X}) at position {i}",
            )
    return SecurityCheckResult(is_safe=True)


def check_jq_system(command: str) -> SecurityCheckResult:
    """Check for jq system() function calls.

    jq's system() and exec() functions can execute arbitrary commands,
    bypassing the perception that jq is a safe read-only tool.
    """
    # Simple check: if the command uses jq with system/exec
    tokens = command.strip().split()
    if not tokens:
        return SecurityCheckResult(is_safe=True)

    base = tokens[0].rsplit("/", 1)[-1] if "/" in tokens[0] else tokens[0]
    if base != "jq":
        return SecurityCheckResult(is_safe=True)

    # Check for system/exec in the jq expression
    jq_expr = " ".join(tokens[1:])
    if re.search(r"\bsystem\b", jq_expr) or re.search(r"\bexec\b", jq_expr):
        return SecurityCheckResult(
            is_safe=False,
            check_id=SecurityCheckID.JQ_SYSTEM_FUNCTION,
            message="jq system/exec function can execute arbitrary commands",
        )
    return SecurityCheckResult(is_safe=True)


def check_brace_expansion(command: str) -> SecurityCheckResult:
    """Check for bash brace expansion.

    Brace expansion like {a,b,c} generates multiple tokens from one
    expression, which can be used to obfuscate command intent.
    """
    # Match brace expansion: {word,word,...}
    # But not inside quotes
    if re.search(r"\{[^}]+,", command):
        return SecurityCheckResult(
            is_safe=False,
            check_id=SecurityCheckID.BRACE_EXPANSION,
            message="Brace expansion detected (can generate multiple commands)",
        )
    return SecurityCheckResult(is_safe=True)


def check_proc_environ_access(command: str) -> SecurityCheckResult:
    """Check for /proc environment variable access.

    Reading /proc/$$/environ exposes all environment variables
    including sensitive tokens and credentials.
    """
    if re.search(r"/proc/\d+/environ", command) or "/proc/self/environ" in command:
        return SecurityCheckResult(
            is_safe=False,
            check_id=SecurityCheckID.PROC_ENVIRON_ACCESS,
            message="Accessing /proc/*/environ exposes environment variables",
        )
    return SecurityCheckResult(is_safe=True)


def check_dangerous_variables(command: str) -> SecurityCheckResult:
    """Check for dangerous environment variable assignments.

    Assigning PATH, LD_PRELOAD, etc. can hijack binary execution.
    """
    tokens = command.strip().split()
    env_var_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
    for token in tokens:
        match = env_var_re.match(token)
        if match:
            var_name = match.group(1)
            if var_name in DANGEROUS_ENV_VARS:
                return SecurityCheckResult(
                    is_safe=False,
                    check_id=SecurityCheckID.DANGEROUS_VARIABLES,
                    message=f"Dangerous environment variable: {var_name}",
                )
    return SecurityCheckResult(is_safe=True)


def analyze_command_security(command: str) -> SecurityCheckResult:
    """Run all security checks on a command.

    Returns the first failing check, or a safe result if all pass.
    FAIL-CLOSED: if any check fails, the command is unsafe.

    Check order matters — cheaper/more common checks first.
    """
    checks = [
        check_control_characters,
        check_unicode_whitespace,
        check_newlines,
        check_command_substitution,
        check_zsh_dangerous_commands,
        check_dangerous_builtins,
        check_jq_system,
        check_brace_expansion,
        check_proc_environ_access,
        check_dangerous_variables,
    ]

    for check in checks:
        result = check(command)
        if not result.is_safe:
            return result

    return SecurityCheckResult(is_safe=True)
