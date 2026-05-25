"""Built-in /dangerous-bg-no-ask command."""

from __future__ import annotations

from ..commands.types import CommandContext, CommandResult, LocalCommand, ResultType


async def handle_dangerous_bg_no_ask(args: str, context: CommandContext) -> CommandResult:
    """Handle /dangerous-bg-no-ask: toggle auto-approve for background agents."""
    current = context.store_get("dangerous_bg_no_ask", False)
    new_value = not current
    context.store_set("dangerous_bg_no_ask", new_value)
    if new_value:
        msg = "⚠️  后台 Agent 自动放行已开启：后台任务将跳过所有权限检查，自动执行。仅影响本次会话。"
    else:
        msg = "后台 Agent 自动放行已关闭：后台任务将正常检查权限。"
    return CommandResult(
        type=ResultType.TEXT,
        content={"message": msg},
    )


def create() -> LocalCommand:
    return LocalCommand(
        name="/dangerous-bg-no-ask",
        description="Toggle auto-approve for background agents (session-only)",
        handler=handle_dangerous_bg_no_ask,
    )