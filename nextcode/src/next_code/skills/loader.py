"""Skill directory loader — scan .nextcode/skills/ directories for Skill files.

Supports two Skill file formats:
- **New format**: ``<skill_dir>/<name>/SKILL.md`` — each skill is a directory
  containing a SKILL.md file. Additional resources can live alongside it.
- **Legacy format**: ``<skill_dir>/<name>.md`` — single Markdown file at the
  top level of the skills directory.

The loader is **lazy**: it only reads the YAML frontmatter at registration
time to extract metadata (name, description, paths, allowed_tools, etc.).
The Markdown body is NOT loaded until the skill is actually invoked via
``get_prompt()``, which reads the file on first call and caches the result.
This keeps startup cost proportional to the number of frontmatter headers,
not the total size of all skill files.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter, _FRONTMATTER_RE
from .create_command import create_skill_command
from ..commands.types import Command

logger = logging.getLogger(__name__)


async def load_skills_from_dir(
    skill_dir: Path,
    source: str = "skills",
    loaded_from: str = "skills",
) -> list[Command]:
    """Load all Skill commands from a directory.

    Only frontmatter metadata is parsed at this point; the Markdown body
    is read lazily when the skill is invoked.

    Args:
        skill_dir: Path to the skills directory (e.g. ``~/.nextcode/skills``).
        source: Origin label for the created commands ('project' | 'user').
        loaded_from: Loading context for the created commands.

    Returns:
        A list of PromptCommand instances. Returns an empty list if the
        directory does not exist or cannot be read.
    """
    if not skill_dir.is_dir():
        return []

    commands: list[Command] = []

    try:
        entries = sorted(skill_dir.iterdir())
    except OSError:
        logger.warning("Failed to list skills directory: %s", skill_dir)
        return []

    for entry in entries:
        if entry.is_dir():
            # New format: <name>/SKILL.md
            skill_md = entry / "SKILL.md"
            if skill_md.exists():
                cmd = await _load_skill_metadata(
                    file_path=skill_md,
                    skill_name=entry.name,
                    source=source,
                    loaded_from=loaded_from,
                    base_dir=str(entry),
                )
                if cmd is not None:
                    commands.append(cmd)

        elif entry.is_file() and entry.suffix == ".md" and entry.name != "SKILL.md":
            # Legacy format: <name>.md
            skill_name = entry.stem
            cmd = await _load_skill_metadata(
                file_path=entry,
                skill_name=skill_name,
                source=source,
                loaded_from="skills",
                base_dir=str(entry.parent),
            )
            if cmd is not None:
                commands.append(cmd)

    return commands


async def _load_skill_metadata(
    file_path: Path,
    skill_name: str,
    source: str,
    loaded_from: str,
    base_dir: str,
) -> Command | None:
    """Load only the frontmatter metadata from a Skill file.

    Does NOT read the Markdown body — that is deferred to get_prompt().
    Reads only the frontmatter lines (up to the closing ---), minimizing
    I/O at startup.
    """
    try:
        frontmatter_text = _read_frontmatter_only(file_path)
    except OSError as e:
        logger.warning("Failed to read skill file %s: %s", file_path, e)
        return None

    frontmatter, _ = parse_frontmatter(frontmatter_text)
    return create_skill_command(
        skill_name=skill_name,
        file_path=str(file_path),
        frontmatter=frontmatter,
        source=source,
        loaded_from=loaded_from,
        base_dir=base_dir,
    )


def _read_frontmatter_only(file_path: Path) -> str:
    """Read only the YAML frontmatter portion of a file.

    Reads line by line and stops after the closing ``---`` delimiter.
    Returns a string containing the full frontmatter block (including both
    ``---`` delimiters) so that ``parse_frontmatter()`` can parse it.
    If no frontmatter is found, returns the first line only (enough for
    parse_frontmatter to determine there's no metadata).

    This avoids reading potentially large Markdown bodies at registration time.
    A typical frontmatter is 5-15 lines (~200 bytes); a typical Skill body
    can be several kilobytes. Reading only the header saves that I/O.
    """
    lines: list[str] = []
    delimiter_count = 0

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            lines.append(line)
            stripped = line.strip()

            # Count --- delimiters (opening and closing)
            if stripped == "---":
                delimiter_count += 1
                if delimiter_count == 2:
                    # Found closing --- — stop here, don't read the body
                    break
            # Safety: if we've read many lines without finding frontmatter,
            # the file likely has no frontmatter. Stop early.
            if len(lines) > 100 and delimiter_count == 0:
                break

    return "".join(lines)


# ── Dynamic skill discovery ──────────────────────────────────────────────


def discover_skill_dirs_for_paths(
    file_paths: list[str],
    cwd: str,
) -> list[Path]:
    """Progressive skill directory discovery — mirrors discoverSkillDirsForPaths.

    Walks upward from each file's directory toward ``cwd``, looking for
    ``.nextcode/skills/`` directories. This enables monorepo setups where
    sub-projects have their own skills that shouldn't be loaded at the top
    level until a file in that sub-project is touched.

    Args:
        file_paths: Absolute paths of files recently accessed by tools.
        cwd: Current working directory (walk stops here).

    Returns:
        Deduplicated list of discovered ``.nextcode/skills/`` directories.
    """
    discovered: list[Path] = []
    seen: set[str] = set()
    resolved_cwd = str(Path(cwd).resolve())

    for file_path in file_paths:
        current = str(Path(file_path).resolve().parent)
        while current.startswith(resolved_cwd + "/") or current == resolved_cwd:
            skill_dir = Path(current) / ".nextcode" / "skills"
            key = str(skill_dir)
            if key not in seen:
                seen.add(key)
                if skill_dir.is_dir():
                    discovered.append(skill_dir)
            if current == resolved_cwd:
                break
            current = str(Path(current).parent)

    return discovered
