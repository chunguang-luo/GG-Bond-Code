"""Built-in /clear command."""

from __future__ import annotations

from ..commands.types import CommandContext, CommandResult, LocalCommand, ResultType


async def handle_clear(args: str, context: CommandContext) -> CommandResult:
    """Handle /clear: clear conversation history and signal caller to rebuild state."""
    context.clear_system_context_cache()
    context.store_set("messages", [])
    return CommandResult(type=ResultType.CLEAR)


def create() -> LocalCommand:
    return LocalCommand(
        name="/clear",
        description="Clear conversation history",
        handler=handle_clear,
    )
