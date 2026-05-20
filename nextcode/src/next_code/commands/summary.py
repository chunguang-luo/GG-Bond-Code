"""Built-in /summary command — show current context summary."""

from __future__ import annotations

from ..commands.types import CommandContext, CommandResult, LocalCommand, ResultType


async def handle_summary(args: str, context: CommandContext) -> CommandResult:
    """Handle /summary: show a summary of the current conversation context."""
    import time

    from ..api.models import get_model_spec
    from ..compact.budget import (
        estimate_token_count,
        get_effective_context_window,
        calculate_token_warning_state,
    )

    model = context.model or context.store_get("model", "deepseek-chat")
    spec = get_model_spec(model)
    messages = context.store_get("messages", [])
    session_start = context.store_get("session_start", 0)

    # ── Context usage ──
    token_usage = estimate_token_count(messages)
    effective = get_effective_context_window(model)
    warning_state = calculate_token_warning_state(token_usage, model)
    pct = effective > 0 and round(token_usage / effective * 100) or 0
    bar_len = 30
    filled = effective > 0 and min(bar_len, round(bar_len * token_usage / effective)) or 0
    bar = "█" * filled + "░" * (bar_len - filled)

    # ── Message breakdown ──
    user_count = 0
    assistant_count = 0
    tool_calls: dict[str, int] = {}
    for msg in messages:
        role = msg.get("role", "")
        if role == "user":
            user_count += 1
        elif role == "assistant":
            assistant_count += 1
            # Count tool uses in assistant content blocks
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name", "unknown")
                        tool_calls[name] = tool_calls.get(name, 0) + 1

    # ── Session duration ──
    if session_start:
        elapsed_s = int(time.time() - session_start)
        if elapsed_s < 60:
            duration = f"{elapsed_s}s"
        elif elapsed_s < 3600:
            duration = f"{elapsed_s // 60}m {elapsed_s % 60}s"
        else:
            h = elapsed_s // 3600
            m = (elapsed_s % 3600) // 60
            duration = f"{h}h {m}m"
    else:
        duration = "N/A"

    # ── Loop state ──
    loop_state = context.loop_state
    turn_count = loop_state.turn_count if loop_state else 0
    last_transition = loop_state.transition.value if loop_state and loop_state.transition else "none"

    # ── Background tasks ──
    try:
        from ..tasks.registry import get_task_registry
        registry = get_task_registry()
        all_tasks = registry.list_all()
        running = sum(1 for t in all_tasks if t.status.value == "running")
        completed = sum(1 for t in all_tasks if t.status.value == "completed")
        failed = sum(1 for t in all_tasks if t.status.value == "failed")
    except Exception:
        running, completed, failed = 0, 0, 0

    # ── Build output ──
    lines = [
        f"Model:       {model}",
        f"Duration:    {duration}",
        f"Turns:       {turn_count}",
        f"Messages:    {user_count} user, {assistant_count} assistant",
        "",
        f"Context:     {bar} {pct}%",
        f"             {token_usage:,} / {effective:,} tokens",
        f"State:       {last_transition}",
        "",
    ]

    if tool_calls:
        lines.append("Tool usage:")
        for name, count in sorted(tool_calls.items(), key=lambda x: -x[1]):
            lines.append(f"  {name}: {count}")
        lines.append("")

    if running or completed or failed:
        lines.append("Background tasks:")
        if running:
            lines.append(f"  Running:   {running}")
        if completed:
            lines.append(f"  Completed: {completed}")
        if failed:
            lines.append(f"  Failed:    {failed}")

    return CommandResult(
        type=ResultType.TEXT,
        content={"message": "\n".join(lines)},
    )


def create() -> LocalCommand:
    return LocalCommand(
        name="/summary",
        description="Show current context summary",
        handler=handle_summary,
    )
