"""Built-in /context command."""

from __future__ import annotations

from ..commands.types import CommandContext, CommandResult, LocalCommand, ResultType


async def handle_context(args: str, context: CommandContext) -> CommandResult:
    """Handle /context: show context window usage."""
    from ..api.models import get_model_spec
    from ..compact.budget import (
        estimate_token_count,
        get_effective_context_window,
        get_auto_compact_threshold,
        calculate_token_warning_state,
    )

    model = context.model or context.store_get("model", "")
    spec = get_model_spec(model)
    messages = context.store_get("messages", [])
    token_usage = estimate_token_count(messages)
    effective = get_effective_context_window(model)
    threshold = get_auto_compact_threshold(model)
    warning_state = calculate_token_warning_state(token_usage, model)

    return CommandResult(
        type=ResultType.CONTEXT_INFO,
        content={
            "model": model,
            "contextWindow": spec.context_window,
            "maxOutputTokens": spec.max_output_tokens,
            "tokenUsage": token_usage,
            "effectiveWindow": effective,
            "autoCompactThreshold": threshold,
            "blockingAt": effective - 3000,
            "messageCount": len(messages),
            "warningState": (
                "blocking" if warning_state.is_at_blocking
                else "auto_compact" if warning_state.is_above_auto_compact
                else "warning" if warning_state.is_above_warning
                else "ok"
            ),
            "percentLeft": warning_state.percent_left,
        },
    )


def create() -> LocalCommand:
    return LocalCommand(
        name="/context",
        description="Show context window usage",
        handler=handle_context,
    )
