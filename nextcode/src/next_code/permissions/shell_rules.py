"""Shell permission rule matching — mirrors shellRuleMatching.ts.

Supports three match types for Bash permission rules:
- exact: "git status" matches only that exact command
- prefix: "npm:*" matches commands starting with "npm "
- wildcard: "git *" matches commands starting with "git" (with trailing space optional)

Handles escaped characters: \\* matches a literal *, \\( matches a literal (.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def _find_first_unescaped(text: str, char: str) -> int:
    """Find the first unescaped occurrence of a character.

    Escaped means preceded by an odd number of backslashes.
    """
    i = 0
    while i < len(text):
        if text[i] == "\\":
            i += 2  # Skip escaped character
            continue
        if text[i] == char:
            return i
        i += 1
    return -1


def _unescape_rule_content(content: str) -> str:
    """Unescape rule content: \\( → (, \\) → ), \\\\ → \\\\, \\* → *."""
    result = []
    i = 0
    while i < len(content):
        if content[i] == "\\" and i + 1 < len(content):
            next_char = content[i + 1]
            if next_char in ("(", ")", "*", "\\"):
                result.append(next_char)
                i += 2
                continue
        result.append(content[i])
        i += 1
    return "".join(result)


def parse_rule_string(rule_string: str) -> tuple[str, str | None]:
    """Parse a permission rule string into (toolName, ruleContent).

    Supports two formats:
    - Settings format: "Bash(git commit:*)" → ("Bash", "git commit:*")
    - Session grant format: "Bash:git commit:*" → ("Bash", "git commit:*")
    - Whole-tool: "Bash", "Bash()", "Bash(*)", "Bash:*" → ("Bash", None)

    Examples:
        "Bash" → ("Bash", None)
        "Bash(git commit:*)" → ("Bash", "git commit:*")
        "Bash:git commit:*" → ("Bash", "git commit:*")
        "Edit:*" → ("Edit", None)
        "Bash(npm run \\(dev\\))" → ("Bash", "npm run (dev)")
        "Bash()" → ("Bash", None)
        "Bash(*)" → ("Bash", None)
        "mcp__server1" → ("mcp__server1", None)

    Handles legacy tool name normalization.
    """
    # Try settings format: ToolName(content)
    open_idx = _find_first_unescaped(rule_string, "(")
    if open_idx != -1:
        tool_name = rule_string[:open_idx]
        raw_content = rule_string[open_idx + 1:]

        # Find matching close paren
        close_idx = _find_first_unescaped(raw_content, ")")
        if close_idx == -1:
            # Malformed — treat entire string as tool name
            return (_normalize_legacy_tool_name(rule_string), None)

        raw_content = raw_content[:close_idx]
        rule_content = _unescape_rule_content(raw_content)

        # Empty content or "*" means "entire tool" — normalize to None
        if not rule_content or rule_content == "*":
            return (_normalize_legacy_tool_name(tool_name), None)

        return (_normalize_legacy_tool_name(tool_name), rule_content)

    # Try session grant format: ToolName:content or ToolName:*
    # Tool names never contain ":", so we can split on the first ":"
    colon_idx = rule_string.find(":")
    if colon_idx != -1:
        tool_name = rule_string[:colon_idx]
        content = rule_string[colon_idx + 1:]
        # "*" or empty means whole-tool
        if not content or content == "*":
            return (_normalize_legacy_tool_name(tool_name), None)
        return (_normalize_legacy_tool_name(tool_name), content)

    # Plain tool name
    return (_normalize_legacy_tool_name(rule_string), None)


def _normalize_legacy_tool_name(name: str) -> str:
    """Map legacy tool names to current names."""
    legacy_map = {
        "Task": "Agent",
        "KillShell": "TaskStop",
        "TodoWrite": "TaskCreate",
        "TodoRead": "TaskList",
    }
    return legacy_map.get(name, name)


# ── Shell rule matching ──────────────────────────────────────────────────────


def match_shell_rule(rule_content: str, command: str) -> bool:
    """Match a shell permission rule against a command.

    Detects the rule type and dispatches to the appropriate matcher:
    - exact: no wildcard characters → exact string match
    - prefix: ends with ":*" → prefix match (e.g. "npm:*" matches "npm install")
    - wildcard: contains "*" → wildcard pattern match

    Args:
        rule_content: The rule content (e.g. "git commit:*", "npm:*", "git status").
        command: The actual command to check.

    Returns:
        True if the rule matches the command.
    """
    if not rule_content or rule_content == "*":
        return True  # Whole-tool rule matches everything

    # Detect rule type
    if rule_content.endswith(":*") and "*" not in rule_content[:-2]:
        # Prefix match: "npm:*" → match commands starting with "npm "
        prefix = rule_content[:-2]
        return _match_prefix(prefix, command)

    if "*" in rule_content:
        # Wildcard match: "git *" → match via wildcard pattern
        return _match_wildcard(rule_content, command)

    # Exact match
    return command == rule_content


def _match_prefix(prefix: str, command: str) -> bool:
    """Match a prefix rule against a command.

    "npm:*" matches "npm install", "npm run build", etc.
    The prefix must match a full token boundary — "npm:*" does NOT match "npx".
    """
    if command == prefix:
        return True
    return command.startswith(prefix + " ")


def _match_wildcard(pattern: str, command: str, case_insensitive: bool = False) -> bool:
    """Match a wildcard pattern against a command.

    Converts * to .* (non-greedy), escapes other regex special chars.
    Supports \\* to match a literal asterisk.

    Special optimization: when the pattern ends with ' *' and has exactly one
    wildcard, the trailing space-and-args is made optional, so 'git *' matches
    both 'git add' and 'git'.

    Mirrors matchWildcardPattern() in shellRuleMatching.ts.
    """
    flags = re.IGNORECASE if case_insensitive else 0

    # Process the pattern character by character to handle escapes
    processed = _process_wildcard_pattern(pattern)

    # Count unescaped stars
    unescaped_stars = processed.count("*")

    # Build regex from processed pattern
    regex_parts: list[str] = []
    for ch in processed:
        if ch == "*":
            regex_parts.append(".*")
        else:
            regex_parts.append(re.escape(ch))

    regex_pattern = "".join(regex_parts)

    # Special case: pattern ends with ' *' and has exactly one wildcard
    # Make the trailing space-and-args optional
    # Check against the original processed pattern, not the regex
    if processed.endswith(" *") and unescaped_stars == 1:
        # Strip the last 2 regex_parts (the escaped space + '.*')
        # and replace with optional group
        regex_pattern = "".join(regex_parts[:-2]) + "( .*)?"

    try:
        return bool(re.fullmatch(regex_pattern, command, flags=flags))
    except re.error:
        return False


def _process_wildcard_pattern(pattern: str) -> str:
    """Process a wildcard pattern, handling escape sequences.

    \\* → literal * (marked)
    \\\\ → literal backslash
    """
    result: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern[i] == "\\" and i + 1 < len(pattern):
            next_char = pattern[i + 1]
            if next_char in ("*", "\\"):
                # Escaped character — keep as literal (not a wildcard)
                result.append(next_char)
                i += 2
                continue
        result.append(pattern[i])
        i += 1
    return "".join(result)