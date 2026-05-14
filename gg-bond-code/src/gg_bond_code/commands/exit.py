"""Built-in /exit command (aliases: /quit, /q)."""

from __future__ import annotations

from ..commands.types import CommandContext, CommandResult, LocalCommand, ResultType


async def handle_exit(args: str, context: CommandContext) -> CommandResult:
    """Handle /exit, /quit, /q: end the session."""
    return CommandResult(
        type=ResultType.SHUTDOWN,
        content={"reason": "user exit"},
    )


def create() -> LocalCommand:
    return LocalCommand(
        name="/exit",
        description="Exit the session",
        handler=handle_exit,
        aliases=["/quit", "/q"],
    )
