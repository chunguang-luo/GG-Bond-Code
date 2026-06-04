"""create_skill_command — convert Markdown + Frontmatter into a PromptCommand.

This is the core of the Skill loading pipeline: a Markdown file with YAML
frontmatter is transformed into a PromptCommand whose ``get_prompt``
callable produces the prompt content sent to the model.

**Lazy loading**: The Markdown body is NOT read at registration time.
Instead, ``file_path`` is stored and the body is read on the first
``get_prompt()`` invocation, then cached for subsequent calls. This
keeps startup cost proportional to frontmatter size, not file size.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .frontmatter import SkillFrontmatter
from ..commands.types import CommandContext, CommandResult, PromptCommand, ResultType

logger = logging.getLogger(__name__)


def create_skill_command(
    skill_name: str,
    file_path: str,
    frontmatter: SkillFrontmatter,
    source: str,
    loaded_from: str,
    base_dir: str | None = None,
) -> PromptCommand:
    """Convert a Skill file path + frontmatter into a PromptCommand.

    The Markdown body is NOT read here — it is loaded lazily on the first
    ``get_prompt()`` call and then cached.

    Args:
        skill_name: The slash command name (e.g. "review").
        file_path: Absolute path to the SKILL.md file.
        frontmatter: Parsed YAML frontmatter metadata (already extracted).
        source: Origin — 'builtin' | 'skills' | 'mcp' | 'plugin' | 'bundled'.
        loaded_from: Loading context — 'skills' | 'bundled' | 'mcp' | None.
        base_dir: Directory containing the skill file (for variable substitution).
    """
    # Capture variables for the closure
    _file_path = file_path
    _frontmatter = frontmatter
    _base_dir = base_dir
    _arg_names = _parse_arg_names(frontmatter.argument_hint)
    # Cache for the Markdown body — populated on first get_prompt() call
    _cached_body: list[str | None] = [None]
    # Track file mtime to invalidate cache when the file changes
    _cached_mtime: list[float | None] = [None]

    async def get_prompt(args: str, context: CommandContext) -> list[dict[str, Any]]:
        """Generate prompt blocks from the skill's Markdown content.

        Reads the file on first invocation and caches the body.
        Re-reads the file if it has been modified since the last read.
        """
        # Check if file changed since last read
        _maybe_invalidate_cache(_file_path, _cached_body, _cached_mtime)

        # Lazy load the Markdown body
        if _cached_body[0] is None:
            _cached_body[0] = _read_body(_file_path)
            _cached_mtime[0] = _get_mtime(_file_path)

        content = _cached_body[0]

        # 1. Argument substitution: ${ARG_NAME} → user-supplied value
        if args and _arg_names:
            parts = args.split(maxsplit=len(_arg_names) - 1) if len(_arg_names) > 1 else [args]
            substituted = False
            for i, arg_name in enumerate(_arg_names):
                if i < len(parts):
                    placeholder = f"${{{arg_name}}}"
                    if placeholder in content:
                        content = content.replace(placeholder, parts[i])
                        substituted = True
            # If no ${VAR} placeholders matched, append user input as instructions
            # so it's not silently discarded
            if not substituted:
                content = content.rstrip() + "\n\n---\nUser instruction: " + args
        elif args:
            # No formal argument names defined — append user input as instructions
            # so it's not silently discarded
            content = content.rstrip() + "\n\n---\nUser instruction: " + args

        # 2. Directory variable substitution: ${NEXTCODE_SKILL_DIR} → skill directory path
        if _base_dir:
            content = content.replace("${NEXTCODE_SKILL_DIR}", _base_dir)

        return [{"type": "text", "text": content}]

    # Placeholder handler — PromptCommands dispatch through get_prompt, not handler.
    async def _noop_handler(args: str, context: CommandContext) -> CommandResult:
        return CommandResult(type=ResultType.TEXT, content={"message": ""})

    return PromptCommand(
        name=f"/{skill_name}",
        description=frontmatter.description or f"Skill: {skill_name}",
        handler=_noop_handler,
        source=source,
        loaded_from=loaded_from,
        progress_message=f"running {skill_name}",
        arg_names=_arg_names,
        allowed_tools=frontmatter.allowed_tools,
        model=frontmatter.model,
        context=frontmatter.context,
        agent=frontmatter.agent,
        effort=frontmatter.effort,
        when_to_use=frontmatter.when_to_use,
        user_invocable=frontmatter.user_invocable,
        paths=frontmatter.paths,
        get_prompt=get_prompt,
    )


def _read_body(file_path: str) -> str:
    """Read the Markdown body (after frontmatter) from a skill file.

    Skips over the YAML frontmatter block (---...---) and reads only the
    body content that follows it. This avoids loading the frontmatter
    text into memory again when the body is lazily loaded.

    Returns the full file content if no frontmatter is found.
    Returns empty string if the file cannot be read.
    """
    try:
        lines: list[str] = []
        delimiter_count = 0
        past_frontmatter = False

        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()

                if not past_frontmatter:
                    # Still skipping frontmatter
                    if stripped == "---":
                        delimiter_count += 1
                        if delimiter_count == 2:
                            past_frontmatter = True
                    continue

                # Past frontmatter — collect body lines
                lines.append(line)

        return "".join(lines)
    except OSError as e:
        logger.warning("Failed to read skill body from %s: %s", file_path, e)
        return ""


def _get_mtime(file_path: str) -> float | None:
    """Get file modification time, or None if the file doesn't exist."""
    try:
        return Path(file_path).stat().st_mtime
    except OSError:
        return None


def _maybe_invalidate_cache(
    file_path: str,
    cached_body: list[str | None],
    cached_mtime: list[float | None],
) -> None:
    """Invalidate the cached body if the file has been modified.

    Compares the current file mtime against the cached mtime.
    If they differ, clears the cache so the next get_prompt() call
    will re-read the file.
    """
    if cached_body[0] is None:
        return  # Nothing cached yet

    current_mtime = _get_mtime(file_path)
    if current_mtime is not None and cached_mtime[0] is not None:
        if current_mtime != cached_mtime[0]:
            cached_body[0] = None
            cached_mtime[0] = None


def _parse_arg_names(hint: str | None) -> list[str]:
    """Parse argument hint into argument names.

    Examples:
        "<files>" → ["files"]
        "<source> <target>" → ["source", "target"]
        "files" → ["files"]
    """
    if not hint:
        return []
    return [a.strip().lstrip("<").rstrip(">") for a in hint.split() if a.strip()]
