"""Built-in /help command."""

from __future__ import annotations

from ..commands.types import CommandContext, CommandResult, LocalCommand, PromptCommand, ResultType


async def handle_help(args: str, context: CommandContext) -> CommandResult:
    """Handle /help: show available commands grouped by type."""
    builtins: list[str] = []
    skills: list[str] = []
    mcp_commands: list[str] = []

    for cmd in context.registry.all_commands():
        if not getattr(cmd, "user_invocable", True):
            continue
        if getattr(cmd, "is_hidden", False):
            continue
        line = f"  {cmd.name:<16} {cmd.description}"
        if isinstance(cmd, LocalCommand):
            builtins.append(line)
        elif isinstance(cmd, PromptCommand):
            if cmd.loaded_from == "mcp":
                mcp_commands.append(line)
            else:
                skills.append(line)

    # Also list MCP tools from ToolRegistry (mcp__ prefixed)
    tool_registry = getattr(context, "tool_registry", None)
    if tool_registry is not None:
        for tool in tool_registry.all_tools():
            if tool.name.startswith("mcp__"):
                # Format: mcp__server__tool → /server:tool
                parts = tool.name.split("__")
                if len(parts) >= 3:
                    display_name = f"/{parts[1]}:{parts[2]}"
                else:
                    display_name = f"/{tool.name}"
                desc = tool.description
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                mcp_commands.append(f"  {display_name:<16} {desc}")

    sections: list[str] = []
    if builtins:
        sections.append("Commands:")
        sections.extend(builtins)
    if skills:
        sections.append("\nSkills:")
        sections.extend(skills)
    if mcp_commands:
        sections.append("\nMCP:")
        sections.extend(mcp_commands)

    help_text = "\n".join(sections)
    return CommandResult(type=ResultType.TEXT, content={"message": help_text})


def create() -> LocalCommand:
    return LocalCommand(
        name="/help",
        description="Show this help message",
        handler=handle_help,
    )
