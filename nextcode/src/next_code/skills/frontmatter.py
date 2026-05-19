"""Frontmatter parser — YAML frontmatter extraction from Markdown Skill files.

Parses the YAML header between `---` delimiters in Markdown files,
extracting metadata needed to create a PromptCommand.

Uses a simple line-by-line parser to avoid a pyyaml dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillFrontmatter:
    """Parsed metadata from a Skill Markdown file's YAML frontmatter."""

    description: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    model: str | None = None
    context: str = "inline"  # 'inline' | 'fork'
    agent: str | None = None
    effort: str | None = None
    when_to_use: str | None = None
    argument_hint: str | None = None
    user_invocable: bool = True
    paths: list[str] = field(default_factory=list)
    hooks: dict[str, Any] = field(default_factory=dict)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n?---\s*\n", re.DOTALL)


def parse_frontmatter(content: str) -> tuple[SkillFrontmatter, str]:
    """Parse YAML frontmatter from a Markdown file.

    Args:
        content: Full Markdown file content, optionally starting with
                 ``---\\n<yaml>\\n---\\n``.

    Returns:
        A tuple of (parsed frontmatter, remaining Markdown body).
        If no frontmatter is found, returns (default frontmatter, original content).
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return SkillFrontmatter(), content

    yaml_str = match.group(1)
    body = content[match.end():]
    fm = _parse_yaml_lines(yaml_str)
    return fm, body


# ── Internal helpers ──────────────────────────────────────────────────────


def _parse_yaml_lines(yaml_str: str) -> SkillFrontmatter:
    """Simple line-by-line YAML parser for flat key-value pairs.

    Handles:
    - ``key: value``  (scalar)
    - ``key: [a, b]`` (inline list)
    - ``key:``        (empty → default)
    - Comments (lines starting with #)
    - Multi-line list blocks (``- item``)
    """
    fm = SkillFrontmatter()
    lines = yaml_str.splitlines()

    current_key: str | None = None
    current_list: list[str] | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Multi-line list item: "  - value"
        if line.startswith("- ") and current_key is not None and current_list is not None:
            item = line[2:].strip().strip('"').strip("'")
            current_list.append(item)
            continue

        if ":" not in line:
            continue

        # Flush previous multi-line list if any
        if current_list is not None and current_key is not None:
            _apply_field(fm, current_key, current_list)
            current_key = None
            current_list = None

        key, _, value = line.partition(":")
        key = key.strip().lower().replace("-", "_")
        value = value.strip()

        # Empty value → start multi-line list collection
        if not value:
            current_key = key
            current_list = []
            continue

        # Inline list: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",")]
            items = [v for v in items if v]  # drop empty
            _apply_field(fm, key, items)
            continue

        # Scalar value
        _apply_field(fm, key, value.strip('"').strip("'"))

    # Flush trailing multi-line list
    if current_list is not None and current_key is not None:
        _apply_field(fm, current_key, current_list)

    return fm


def _apply_field(fm: SkillFrontmatter, key: str, value: Any) -> None:
    """Set a field on the SkillFrontmatter instance."""
    if key == "description":
        fm.description = str(value)
    elif key == "allowed_tools":
        fm.allowed_tools = list(value) if isinstance(value, list) else [str(value)]
    elif key == "model":
        fm.model = str(value)
    elif key == "context":
        fm.context = str(value)
    elif key == "agent":
        fm.agent = str(value)
    elif key == "effort":
        fm.effort = str(value)
    elif key == "when_to_use":
        fm.when_to_use = str(value)
    elif key == "argument_hint":
        fm.argument_hint = str(value)
    elif key == "user_invocable":
        if isinstance(value, bool):
            fm.user_invocable = value
        elif isinstance(value, str):
            fm.user_invocable = value.lower() in ("true", "yes", "1")
        elif isinstance(value, list):
            # shouldn't happen but be safe
            pass
    elif key == "paths":
        fm.paths = list(value) if isinstance(value, list) else [str(value)]
    elif key == "hooks":
        fm.hooks = value if isinstance(value, dict) else {}
