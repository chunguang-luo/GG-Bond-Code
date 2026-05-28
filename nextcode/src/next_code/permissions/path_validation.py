"""Path validation and safety checks — mirrors pathValidation.ts.

Multi-layered path validation for file operations:
1. validatePath() — pre-validation security checks (TOCTOU prevention)
2. isPathAllowed() — five-step allow/deny decision pipeline
3. isDangerousRemovalPath() — catastrophic path detection
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Glob characters that indicate a pattern, not a literal path
_GLOB_PATTERN_RE = re.compile(r"[*?\[\]{}]")

# Windows drive root: C:\, D:\, etc.
_WINDOWS_DRIVE_ROOT_RE = re.compile(r"^[A-Za-z]:\\$")

# Windows drive direct child: C:\Windows, D:\Users, etc.
_WINDOWS_DRIVE_CHILD_RE = re.compile(r"^[A-Za-z]:\\[^\\]+$")

# Shell expansion patterns
_SHELL_EXPANSION_RE = re.compile(r"[\$%]")

# UNC path prefix
_UNC_PREFIX_RE = re.compile(r"^\\\\")


def _forward_slash(path: str) -> str:
    """Normalize path separators to forward slashes."""
    return path.replace("\\", "/")


def expand_tilde(path: str) -> str:
    """Expand ~ and ~/ to the user's home directory.

    Does NOT expand ~user, ~+, ~- variants — these are TOCTOU risks
    because the expansion may differ between validation and shell execution.
    """
    if path == "~":
        return str(Path.home())
    if path.startswith("~/"):
        return str(Path.home() / path[2:])
    return path


def contains_vulnerable_unc_path(path: str) -> bool:
    """Check if path contains a UNC network path (potential credential leak)."""
    return bool(_UNC_PREFIX_RE.match(path))


# ── validatePath(): pre-validation security checks ──────────────────────────


def validate_path(
    path: str,
    cwd: str,
    operation_type: str,  # "read" | "write" | "create" | "delete"
) -> tuple[bool, str]:
    """Validate a path before permission checking.

    Performs security checks to prevent TOCTOU attacks where the path
    would be expanded differently during validation vs. shell execution.

    Args:
        path: The raw path from tool parameters.
        cwd: Current working directory.
        operation_type: Type of operation (read, write, create, delete).

    Returns:
        (allowed, reason) — (True, "") if valid, (False, reason) if blocked.
    """
    # Strip surrounding quotes
    clean_path = path.strip().strip("'\"")

    # SECURITY: Block UNC network paths (can leak credentials)
    if contains_vulnerable_unc_path(clean_path):
        return False, "UNC network paths require manual approval"

    # SECURITY: Block tilde expansion variants (~user, ~+, ~-)
    # expand_tilde only handles ~ and ~/, so other variants would cause
    # validation-shell inconsistency
    if clean_path.startswith("~") and not (clean_path == "~" or clean_path.startswith("~/")):
        return False, "Tilde expansion variants require manual approval"

    # SECURITY: Block shell expansion syntax
    # $VAR, ${VAR}, $(cmd), %VAR% are literal during validation but
    # would be expanded by the shell during execution
    if _SHELL_EXPANSION_RE.search(clean_path):
        return False, "Shell expansion syntax requires manual approval"

    # SECURITY: Block paths starting with = (MSYS/Cygwin path conversion)
    if clean_path.startswith("="):
        return False, "Shell expansion syntax requires manual approval"

    # SECURITY: Write operations must not use glob patterns
    # Glob patterns during validation only check the base directory,
    # but the actual file operations would match multiple files
    if operation_type in ("write", "create", "delete") and _GLOB_PATTERN_RE.search(clean_path):
        return False, "Glob patterns are not allowed in write operations"

    return True, ""


# ── isDangerousRemovalPath(): catastrophic path detection ────────────────────


def is_dangerous_removal_path(resolved_path: str) -> bool:
    """Check if a path is too dangerous to delete.

    Prevents deletion of root directories, home directories, system directories,
    and wildcard-based deletions.

    Args:
        resolved_path: The fully resolved absolute path.

    Returns:
        True if the path is dangerous to delete.
    """
    forward = _forward_slash(resolved_path)
    normalized = os.path.normpath(resolved_path)
    normalized_home = os.path.normpath(str(Path.home()))

    # Wildcard deletions
    if forward == "*" or forward.endswith("/*"):
        return True

    # Root directory
    if normalized == "/":
        return True

    # Windows drive roots: C:\, D:\
    if _WINDOWS_DRIVE_ROOT_RE.match(normalized):
        return True

    # Home directory
    if normalized == normalized_home:
        return True

    # Direct children of root: /usr, /tmp, /etc, /bin, /opt, /var, /home
    if os.path.dirname(normalized) == "/":
        return True

    # Windows drive direct children: C:\Windows, C:\Users
    if _WINDOWS_DRIVE_CHILD_RE.match(normalized):
        return True

    return False


# ── isPathAllowed(): five-step validation pipeline ───────────────────────────


# Paths that are always safe for internal edits (plan files, scratchpad, etc.)
_INTERNAL_EDITABLE_PATHS: set[str] = {
    ".nextcode/plan.md",
    ".nextcode/scratchpad.md",
}

# Paths protected by safety check (will trigger ASK even in bypass mode)
_PROTECTED_DIR_PREFIXES: tuple[str, ...] = (
    ".git/",
    ".claude/",
    ".vscode/",
    ".idea/",
)


def _is_in_protected_dir(resolved_path: str, cwd: str) -> bool:
    """Check if a resolved path falls within a protected directory."""
    try:
        rel = os.path.relpath(resolved_path, cwd)
    except ValueError:
        return False

    forward = _forward_slash(rel)
    for prefix in _PROTECTED_DIR_PREFIXES:
        if forward == prefix.rstrip("/") or forward.startswith(prefix):
            return True
    return False


def _is_in_working_dir(resolved_path: str, cwd: str) -> bool:
    """Check if a resolved path is within the working directory."""
    try:
        rel = os.path.relpath(resolved_path, cwd)
    except ValueError:
        return False
    return not rel.startswith("..") and not os.path.isabs(rel)


def is_path_allowed(
    resolved_path: str,
    cwd: str,
    operation_type: str,  # "read" | "write" | "create" | "delete"
    *,
    allow_rules: list[str] | None = None,
    deny_rules: list[str] | None = None,
    permission_mode: str = "default",
) -> tuple[bool, str]:
    """Five-step path validation pipeline.

    Priority order:
    1. Deny rules (highest priority)
    2. Internal editable paths (plan files, scratchpad)
    3. Write safety check (protected dirs)
    4. Working directory check (auto-allow for read ops and acceptEdits mode)
    5. Allow rules
    6. Default deny

    Args:
        resolved_path: Fully resolved absolute path.
        cwd: Current working directory.
        operation_type: "read", "write", "create", or "delete".
        allow_rules: List of allow rule patterns for paths.
        deny_rules: List of deny rule patterns for paths.
        permission_mode: Current permission mode.

    Returns:
        (allowed, reason) tuple.
    """
    # Step 1: Deny rules (highest priority)
    if deny_rules:
        for rule in deny_rules:
            if _path_matches_rule(resolved_path, cwd, rule):
                return False, f"Denied by rule: {rule}"

    # Step 2: Internal editable paths (only for write/create operations)
    if operation_type != "read":
        if _is_internal_editable_path(resolved_path, cwd):
            return True, "Internal editable path"

    # Step 3: Write safety check — protected directories
    if operation_type != "read":
        if _is_in_protected_dir(resolved_path, cwd):
            return False, (
                f"Writing to protected directory requires manual approval. "
                f"Use session-level allow rule to authorize."
            )

    # Step 4: Working directory check
    if _is_in_working_dir(resolved_path, cwd):
        if operation_type == "read":
            return True, "Read within working directory"
        if permission_mode == "acceptEdits":
            return True, "Write within working directory (acceptEdits mode)"

    # Step 5: Allow rules
    if allow_rules:
        for rule in allow_rules:
            if _path_matches_rule(resolved_path, cwd, rule):
                return True, f"Allowed by rule: {rule}"

    # Step 6: Default deny for writes outside working directory
    if operation_type != "read":
        return False, "Write outside working directory requires manual approval"

    # Read outside working directory — deny by default
    return False, "Read outside working directory requires manual approval"


def _is_internal_editable_path(resolved_path: str, cwd: str) -> bool:
    """Check if path is an internal editable file (plan, scratchpad, etc.)."""
    try:
        rel = os.path.relpath(resolved_path, cwd)
    except ValueError:
        return False
    forward = _forward_slash(rel)
    return forward in _INTERNAL_EDITABLE_PATHS


def _path_matches_rule(resolved_path: str, cwd: str, rule: str) -> bool:
    """Check if a resolved path matches a permission rule.

    Rules can be:
    - Absolute path: "/home/user/project/src/*"
    - Relative path: "src/*"
    - Tool:path format: "Edit:/home/user/project/*"
    """
    # Strip tool prefix if present (e.g. "Edit:/path" → "/path")
    if ":" in rule and not rule.startswith("/") and not rule.startswith("~"):
        colon_idx = rule.index(":")
        # Check if the part before colon looks like a tool name (not a path)
        before_colon = rule[:colon_idx]
        if before_colon and not before_colon.startswith("/") and "\\" not in before_colon:
            rule = rule[colon_idx + 1:]

    # Resolve the rule path
    if rule.startswith("/"):
        rule_path = rule
    elif rule.startswith("~/"):
        rule_path = str(Path.home() / rule[2:])
    else:
        rule_path = str(Path(cwd) / rule)

    # Glob matching
    import fnmatch
    return fnmatch.fnmatch(resolved_path, rule_path)


# ── Safety check for auto-edit operations ───────────────────────────────────


def check_path_safety_for_auto_edit(
    resolved_path: str,
    cwd: str,
) -> tuple[bool, str]:
    """Check if a path is safe for automatic editing (no user confirmation).

    This is the safety check that runs even in bypassPermissions mode.
    It protects critical project files and directories.

    Args:
        resolved_path: Fully resolved absolute path.
        cwd: Current working directory.

    Returns:
        (safe, reason) — (True, "") if safe, (False, reason) if blocked.
    """
    # Block writes to protected directories
    if _is_in_protected_dir(resolved_path, cwd):
        return False, "Path is in a protected directory (.git, .claude, etc.)"

    # Block writes to paths with ".." traversal attempts
    if ".." in _forward_slash(resolved_path).split("/"):
        return False, "Path traversal detected"

    return True, ""
