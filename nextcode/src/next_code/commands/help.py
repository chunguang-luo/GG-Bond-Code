"""Built-in /help command."""

from __future__ import annotations

from ..commands.types import CommandContext, CommandResult, LocalCommand, ResultType


async def handle_help(args: str, context: CommandContext) -> CommandResult:
    """Handle /help: show available commands (generated from registry)."""
    lines = []
    for cmd in context.registry.all_commands():
        # Show primary name only, left-padded for alignment
        lines.append(f"  {cmd.name:<12} - {cmd.description}")
    help_text = "Available commands:\n" + "\n".join(lines)
    return CommandResult(type=ResultType.TEXT, content={"message": help_text})


def create() -> LocalCommand:
    return LocalCommand(
        name="/help",
        description="Show this help message",
        handler=handle_help,
    )
