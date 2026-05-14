"""Built-in /model command."""

from __future__ import annotations

from ..commands.types import CommandContext, CommandResult, LocalCommand, ResultType


async def handle_model(args: str, context: CommandContext) -> CommandResult:
    """Handle /model: show current model."""
    model = context.store_get("model", "unknown")
    return CommandResult(
        type=ResultType.TEXT,
        content={"message": f"Current model: {model}"},
    )


def create() -> LocalCommand:
    return LocalCommand(
        name="/model",
        description="Show current model",
        handler=handle_model,
    )
