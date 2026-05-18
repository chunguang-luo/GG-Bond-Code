"""Built-in /thinking command."""

from __future__ import annotations

from ..commands.types import CommandContext, CommandResult, LocalCommand, ResultType


async def handle_thinking(args: str, context: CommandContext) -> CommandResult:
    """Handle /thinking: toggle thinking display."""
    current = context.store_get("ui.show_thinking", False)
    new_value = not current
    context.store_set("ui.show_thinking", new_value)
    state = "ON" if new_value else "OFF"
    return CommandResult(
        type=ResultType.TEXT,
        content={"message": f"Thinking display: {state}", "enabled": new_value},
    )


def create() -> LocalCommand:
    return LocalCommand(
        name="/thinking",
        description="Toggle thinking display",
        handler=handle_thinking,
    )
