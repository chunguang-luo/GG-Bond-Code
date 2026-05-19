"""SkillTool — allows the model to invoke slash commands via tool calls.

When the model needs to trigger a skill (e.g. to run /review on code it just
edited), it calls the Skill tool with the skill name and optional arguments.
The tool looks up the PromptCommand in the CommandRegistry, generates the
prompt via get_prompt, and returns the resulting text.
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult


class SkillTool(Tool):
    name = "Skill"
    description = (
        "Execute a skill by name. Skills are slash commands that can be "
        "invoked by the model during conversation. Use this when the user's "
        "request matches a known skill, or when a skill's when_to_use trigger "
        "matches the current context."
    )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": (
                        "The skill name to invoke (without the leading slash). "
                        "Examples: 'review', 'commit', 'simplify'."
                    ),
                },
                "args": {
                    "type": "string",
                    "description": "Optional arguments for the skill.",
                },
            },
            "required": ["skill"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        from ..commands.types import PromptCommand

        ctx = self._context
        if ctx is None:
            return ToolResult(output="Skill tool: no context available", error=True)

        # Access the command registry from ToolUseContext
        command_registry = getattr(ctx, "command_registry", None)
        if command_registry is None:
            return ToolResult(output="Skill tool: no command registry available", error=True)

        skill_name = params["skill"]
        # Normalize: accept with or without leading slash
        if not skill_name.startswith("/"):
            skill_name = f"/{skill_name}"

        args = params.get("args", "")

        command = command_registry.lookup(skill_name)
        if command is None:
            available = ", ".join(
                cmd.name for cmd in command_registry.all_commands()
                if getattr(cmd, "user_invocable", True)
            )
            return ToolResult(
                output=f"Unknown skill: {skill_name}. Available: {available}",
                error=True,
            )

        if not isinstance(command, PromptCommand):
            return ToolResult(
                output=f"'{skill_name}' is not a prompt-based skill.",
                error=True,
            )

        if command.get_prompt is None:
            return ToolResult(
                output=f"Skill '{skill_name}' has no prompt generator.",
                error=True,
            )

        # Build a minimal CommandContext for the prompt generator
        from ..commands.types import CommandContext

        cmd_context = CommandContext(
            model=getattr(ctx, "get_state", lambda k: None)("model"),
            store_get=ctx.get_state,
            store_set=ctx.set_state,
            loop_state=None,
            clear_system_context_cache=lambda: None,
            registry=command_registry,
        )

        try:
            prompt_blocks = await command.get_prompt(args, cmd_context)
        except Exception as e:
            return ToolResult(output=f"Skill '{skill_name}' failed: {e}", error=True)

        # Flatten prompt blocks into text
        text = "\n".join(
            block.get("text", "")
            for block in prompt_blocks
            if block.get("type") == "text"
        )
        return ToolResult(output=text)

    def is_read_only(self, params: dict[str, Any]) -> bool:
        """SkillTool is read-only — it generates prompts, doesn't modify state."""
        return True
