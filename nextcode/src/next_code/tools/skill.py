"""SkillTool — allows the model to invoke slash commands via tool calls.

When the model needs to trigger a skill (e.g. to run /doc on a document it just
read), it calls the Skill tool with the skill name and optional arguments.
The tool looks up the PromptCommand in the CommandRegistry, generates the
prompt via get_prompt, and returns the resulting text.

The tool description dynamically includes available skill names so the model
knows what skills it can invoke without trial-and-error.
"""
from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult

_STATIC_DESCRIPTION = (
    "Execute a skill by name, or list available skills. "
    "Skills are slash commands that can be invoked by the model during conversation."
)


class SkillTool(Tool):
    name = "Skill"
    description = _STATIC_DESCRIPTION

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["invoke", "list"],
                    "description": (
                        "Action to perform: 'invoke' to run a skill (default), "
                        "'list' to see all available skills with descriptions."
                    ),
                },
                "skill": {
                    "type": "string",
                    "description": (
                        "The skill name to invoke (without the leading slash). "
                        "Examples: 'doc', 'review', 'commit', 'simplify'. "
                        "Only used with action='invoke'."
                    ),
                },
                "args": {
                    "type": "string",
                    "description": "Optional arguments for the skill.",
                },
            },
            "required": [],
        }

    def to_api_format(self, family: str = "openai") -> dict[str, Any]:
        """Override to inject available skill names into the description.

        This is the key mechanism that lets the model know which skills
        are available — the description is dynamically enriched each time
        the tool definition is sent to the API.
        """
        desc = self._build_dynamic_description()
        schema = self.get_schema()

        if family == "anthropic":
            return {
                "name": self.name,
                "description": desc,
                "input_schema": schema,
            }
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": desc,
                "parameters": schema,
            },
        }

    def _build_dynamic_description(self) -> str:
        """Build description with available skill names injected.

        Format: static description + list of available skills with descriptions.
        Only includes prompt-based skills (SKILL.md files), not built-in commands.
        Falls back to static description if context/registry is unavailable.
        """
        parts = [_STATIC_DESCRIPTION]

        ctx = self._context
        if ctx is None:
            return _STATIC_DESCRIPTION

        command_registry = getattr(ctx, "command_registry", None)
        if command_registry is None:
            return _STATIC_DESCRIPTION

        from ..commands.types import PromptCommand

        skills: list[str] = []
        for cmd in command_registry.all_commands():
            if not getattr(cmd, "user_invocable", True):
                continue
            if getattr(cmd, "is_hidden", False):
                continue
            if isinstance(cmd, PromptCommand) and cmd.loaded_from in ("skills", "bundled", "mcp"):
                arg_hint = ""
                if cmd.arg_names:
                    arg_hint = " <" + "> <".join(cmd.arg_names) + ">"
                skills.append(f"  - {cmd.name}{arg_hint}: {cmd.description}")

        if skills:
            parts.append("\n\nAvailable skills:")
            parts.extend(skills)

        return "\n".join(parts)

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        from ..commands.types import PromptCommand

        ctx = self._context
        if ctx is None:
            return ToolResult(output="Skill tool: no context available", error=True)

        command_registry = getattr(ctx, "command_registry", None)
        if command_registry is None:
            return ToolResult(output="Skill tool: no command registry available", error=True)

        action = params.get("action", "invoke")

        if action == "list":
            return self._list_skills(command_registry)

        # Default action: invoke
        skill_name = params.get("skill", "")
        if not skill_name:
            return self._list_skills(command_registry)

        # Normalize: accept with or without leading slash
        if not skill_name.startswith("/"):
            skill_name = f"/{skill_name}"

        args = params.get("args", "")

        command = command_registry.lookup(skill_name)
        if command is None:
            return self._unknown_skill_error(command_registry, skill_name)

        if not isinstance(command, PromptCommand):
            return ToolResult(
                output=f"'{skill_name}' is a built-in command, not a prompt-based skill. Only prompt-based skills can be invoked via the Skill tool.",
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
            tool_registry=ctx.registry,
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

    def _list_skills(self, command_registry: Any) -> ToolResult:
        """List all available skills with descriptions."""
        from ..commands.types import PromptCommand

        lines = ["Available skills:"]
        for cmd in command_registry.all_commands():
            if not getattr(cmd, "user_invocable", True):
                continue
            if getattr(cmd, "is_hidden", False):
                continue
            if isinstance(cmd, PromptCommand) and cmd.loaded_from in ("skills", "bundled", "mcp"):
                source_tag = f" [{cmd.source}]" if cmd.source != "builtin" else ""
                arg_hint = ""
                if cmd.arg_names:
                    arg_hint = " <" + "> <".join(cmd.arg_names) + ">"
                lines.append(f"  {cmd.name}{arg_hint} — {cmd.description}{source_tag}")

        if len(lines) == 1:
            lines.append("  (no skills loaded)")

        return ToolResult(output="\n".join(lines))

    def _unknown_skill_error(self, command_registry: Any, skill_name: str) -> ToolResult:
        """Generate a helpful error for an unknown skill."""
        from ..commands.types import PromptCommand

        skills = []
        for cmd in command_registry.all_commands():
            if not getattr(cmd, "user_invocable", True):
                continue
            if isinstance(cmd, PromptCommand) and cmd.loaded_from in ("skills", "bundled", "mcp"):
                skills.append(f"{cmd.name} — {cmd.description}")

        lines = [f"Unknown skill: {skill_name}"]
        if skills:
            lines.append("Available skills:")
            for s in skills:
                lines.append(f"  {s}")
        else:
            lines.append("No skills are currently loaded.")

        return ToolResult(output="\n".join(lines), error=True)

    def is_read_only(self, params: dict[str, Any]) -> bool:
        """SkillTool is read-only — it generates prompts, doesn't modify state."""
        return True
