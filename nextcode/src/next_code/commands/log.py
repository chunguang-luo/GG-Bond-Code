"""Built-in /log command."""

from __future__ import annotations

from ..commands.types import CommandContext, CommandResult, LocalCommand, ResultType


async def handle_log(args: str, context: CommandContext) -> CommandResult:
    """Handle /log: show last query transition log."""
    loop_state = context.loop_state
    if loop_state is None:
        return CommandResult(
            type=ResultType.TEXT,
            content={"message": "No transition log available. Run a query first."},
        )

    log_text = loop_state.format_log()
    if not log_text or not log_text.strip():
        return CommandResult(
            type=ResultType.TEXT,
            content={"message": "No transition log available. Run a query first."},
        )

    return CommandResult(
        type=ResultType.TEXT,
        content={"message": log_text},
    )


def create() -> LocalCommand:
    return LocalCommand(
        name="/log",
        description="Show last query transition log",
        handler=handle_log,
    )
