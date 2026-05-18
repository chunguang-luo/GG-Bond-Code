"""Built-in /compact command."""

from __future__ import annotations

from ..commands.types import CommandContext, CommandResult, LocalCommand, ResultType


async def handle_compact(args: str, context: CommandContext) -> CommandResult:
    """Handle /compact: compact conversation to save context."""
    context.clear_system_context_cache()

    messages = context.store_get("messages", [])
    if not messages:
        return CommandResult(
            type=ResultType.TEXT,
            content={"message": "No messages to compact."},
        )

    from ..compact.manager import CompactManager, CompactLevel
    model = context.model or context.store_get("model", "deepseek-chat")
    manager = CompactManager(model=model)
    compacted, reason = await manager.execute(CompactLevel.FULL, messages)
    context.store_set("messages", compacted)

    return CommandResult(
        type=ResultType.COMPACT_COMPLETE,
        content={"reason": reason},
    )


def create() -> LocalCommand:
    return LocalCommand(
        name="/compact",
        description="Compact conversation to save context",
        handler=handle_compact,
    )
