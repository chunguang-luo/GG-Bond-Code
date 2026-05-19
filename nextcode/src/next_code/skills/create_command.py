"""create_skill_command — convert Markdown + Frontmatter into a PromptCommand.

This is the core of the Skill loading pipeline: a Markdown file with YAML
frontmatter is transformed into a PromptCommand whose ``get_prompt``
callable produces the prompt content sent to the model.
"""

from __future__ import annotations

import re
from typing import Any

from .frontmatter import SkillFrontmatter
from ..commands.types import CommandContext, CommandResult, PromptCommand, ResultType


def create_skill_command(
    skill_name: str,
    markdown_content: str,
    frontmatter: SkillFrontmatter,
    source: str,
    loaded_from: str,
    base_dir: str | None = None,
) -> PromptCommand:
    """Convert a Markdown Skill into a PromptCommand.

    Args:
        skill_name: The slash command name (e.g. "review").
        markdown_content: The Markdown body (after frontmatter extraction).
        frontmatter: Parsed YAML frontmatter metadata.
        source: Origin — 'builtin' | 'skills' | 'mcp' | 'plugin' | 'bundled'.
        loaded_from: Loading context — 'skills' | 'bundled' | 'mcp' | None.
        base_dir: Directory containing the skill file (for variable substitution).
    """
    # Capture variables for the closure
    _content = markdown_content
    _frontmatter = frontmatter
    _base_dir = base_dir
    _loaded_from = loaded_from
    _arg_names = _parse_arg_names(frontmatter.argument_hint)

    async def get_prompt(args: str, context: CommandContext) -> list[dict[str, Any]]:
        """Generate prompt blocks from the skill's Markdown content."""
        content = _content

        # 1. Argument substitution: ${ARG_NAME} → user-supplied value
        if args and _arg_names:
            parts = args.split(maxsplit=len(_arg_names) - 1) if len(_arg_names) > 1 else [args]
            for i, arg_name in enumerate(_arg_names):
                if i < len(parts):
                    content = content.replace(f"${{{arg_name}}}", parts[i])

        # 2. Directory variable substitution: ${NEXTCODE_SKILL_DIR} → skill directory path
        if _base_dir:
            content = content.replace("${NEXTCODE_SKILL_DIR}", _base_dir)

        return [{"type": "text", "text": content}]

    # Placeholder handler — PromptCommands dispatch through get_prompt, not handler.
    # The handler field is required by CommandBase but unused for PromptCommands.
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
