"""Query runner — the core conversation loop, mirrors query.ts."""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Awaitable

from .api.client import stream_message, get_model_family
from .prompts.system import build_system_prompt
from .state.context import ToolUseContext, create_store_context
from .state.store import Store
from .tools.base import ToolRegistry, ToolResult, create_default_registry
from .permissions.manager import PermissionManager, PermissionDecision
from .context.system import get_system_context, format_system_context, clear_system_context_cache
from .context.user import get_user_context, prepend_user_context


@dataclass
class QueryEvent:
    """Event emitted during query execution."""
    type: str  # "text" | "tool_start" | "tool_use" | "tool_result" | "error" | "thinking"
    content: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_result: str = ""
    tool_error: bool = False
    tool_purpose: str = ""  # model's text before this tool call, explaining intent
    tool_use_id: str = ""


class QueryRunner:
    """Drive the conversation loop: user message → API → tool execution → repeat."""

    def __init__(
        self,
        model: str | None = None,
        tool_registry: ToolRegistry | None = None,
        max_turns: int = 50,
        permission_callback: Callable[[str, dict[str, Any]], Awaitable[PermissionDecision]] | None = None,
        context: ToolUseContext | None = None,
    ) -> None:
        # Build or use provided context
        if context is not None:
            self._context = context
            self._owns_context = False
        else:
            store = Store()
            self._context = create_store_context(
                store=store,
                registry=tool_registry or create_default_registry(),
            )
            self._owns_context = True

        ctx = self._context
        self.model = model or ctx.get_state("model") or "deepseek-chat"
        self.family = get_model_family(self.model)
        self.max_turns = max_turns
        self.system_prompt = build_system_prompt(cwd=ctx.get_state("cwd"))
        self._permission_callback = permission_callback

    @property
    def permissions(self) -> PermissionManager:
        return self._context.permissions

    @permissions.setter
    def permissions(self, value: PermissionManager) -> None:
        self._context.permissions = value

    @property
    def registry(self) -> ToolRegistry:
        return self._context.registry

    async def run(self, user_message: str) -> AsyncIterator[QueryEvent]:
        """Run a single user message through the conversation loop."""
        ctx = self._context

        # Get contexts (computed on demand with caching)
        system_ctx = get_system_context(ctx.get_state("cwd"))
        user_ctx = get_user_context(ctx.get_state("cwd"))

        # Build full system prompt
        static_sections = build_system_prompt(cwd=ctx.get_state("cwd"))
        dynamic_sections = [format_system_context(system_ctx)] if system_ctx else []

        full_system_prompt = [
            *static_sections,
            *dynamic_sections,
        ]

        # Prepare messages with user context
        messages: list[dict[str, Any]] = ctx.get_state("messages") or []
        messages.append({"role": "user", "content": user_message})
        messages = prepend_user_context(messages, user_ctx)

        tools = ctx.registry.to_api_format(self.family)

        try:
            for _ in range(self.max_turns):
                text_parts: list[str] = []
                thinking_parts: list[str] = []
                tool_use_blocks: list[dict[str, Any]] = []

                try:
                    async for evt in stream_message(
                        messages=messages,
                        tools=tools,
                        system=full_system_prompt,
                        model=self.model,
                    ):
                        if evt["type"] == "text_delta":
                            text_parts.append(evt["text"])
                            yield QueryEvent(type="text", content=evt["text"])
                        elif evt["type"] == "thinking_delta":
                            thinking_parts.append(evt["thinking"])
                            yield QueryEvent(type="thinking", content=evt["thinking"])
                        elif evt["type"] == "tool_start":
                            yield QueryEvent(
                                type="tool_start",
                                tool_name=evt["name"],
                                tool_input={"id": evt["id"]},
                                tool_use_id=evt["id"],
                            )
                        elif evt["type"] == "tool_input_delta":
                            # Internal detail — skip for now
                            pass
                        elif evt["type"] == "tool_end":
                            tool_use_blocks.append({
                                "id": evt["id"],
                                "name": evt["name"],
                                "input": evt["input"],
                            })
                            yield QueryEvent(
                                type="tool_use",
                                content=f"Using tool: {evt['name']}",
                                tool_name=evt["name"],
                                tool_input=evt["input"],
                                tool_use_id=evt["id"],
                                tool_purpose=("".join(text_parts).strip()
                                              or "".join(thinking_parts[-3:]).strip()),
                            )
                        elif evt["type"] == "tool_use":
                            # OpenAI backend still uses this event type
                            tool_use_blocks.append(evt)
                            yield QueryEvent(
                                type="tool_use",
                                content=f"Using tool: {evt['name']}",
                                tool_name=evt["name"],
                                tool_input=evt["input"],
                                tool_use_id=evt.get("id", ""),
                                tool_purpose=("".join(thinking_parts).strip()),
                            )
                except Exception as e:
                    tb = traceback.format_exc()
                    yield QueryEvent(type="error", content=f"{e}\n{tb}")
                    return

                # Build assistant message for history
                assistant_content = "".join(text_parts)
                has_tool_use = len(tool_use_blocks) > 0

                if self.family == "anthropic":
                    # Anthropic uses content blocks list
                    content_blocks: list[dict[str, Any]] = []
                    if assistant_content:
                        content_blocks.append({"type": "text", "text": assistant_content})
                    for tb in tool_use_blocks:
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tb["id"],
                            "name": tb["name"],
                            "input": tb["input"],
                        })
                    messages.append({"role": "assistant", "content": content_blocks})
                else:
                    # OpenAI uses content + tool_calls
                    # content must not be None when there are no tool_calls
                    msg: dict[str, Any] = {"role": "assistant", "content": assistant_content or ""}
                    if has_tool_use:
                        msg["tool_calls"] = [
                            {
                                "id": tb["id"],
                                "type": "function",
                                "function": {
                                    "name": tb["name"],
                                    "arguments": json.dumps(tb["input"]),
                                },
                            }
                            for tb in tool_use_blocks
                        ]
                    messages.append(msg)

                # If no tool use, we're done
                if not has_tool_use:
                    break

                # Execute tools and collect results
                if self.family == "anthropic":
                    # Anthropic: all tool_results in a single user message
                    tool_results_content: list[dict[str, Any]] = []
                    for tb in tool_use_blocks:
                        # Emit tool_start for timing (if not already sent via streaming)
                        yield QueryEvent(
                            type="tool_start",
                            tool_name=tb["name"],
                            tool_use_id=tb["id"],
                        )
                        decision = await self._check_permission(tb["name"], tb["input"])
                        if decision == PermissionDecision.DENY:
                            result = ToolResult(output="Permission denied", error=True)
                        else:
                            result = await self._execute_tool(tb["name"], tb["input"])

                        yield QueryEvent(
                            type="tool_result",
                            tool_name=tb["name"],
                            tool_result=result.output,
                            tool_error=result.error,
                            tool_use_id=tb["id"],
                        )

                        tool_results_content.append({
                            "type": "tool_result",
                            "tool_use_id": tb["id"],
                            "content": result.output,
                            "is_error": result.error,
                        })

                    messages.append({"role": "user", "content": tool_results_content})
                else:
                    # OpenAI: each tool result is a separate message
                    for tb in tool_use_blocks:
                        # Emit tool_start for timing (OpenAI backend doesn't stream tool_start)
                        yield QueryEvent(
                            type="tool_start",
                            tool_name=tb["name"],
                            tool_use_id=tb["id"],
                        )
                        decision = await self._check_permission(tb["name"], tb["input"])
                        if decision == PermissionDecision.DENY:
                            result = ToolResult(output="Permission denied", error=True)
                        else:
                            result = await self._execute_tool(tb["name"], tb["input"])

                        yield QueryEvent(
                            type="tool_result",
                            tool_name=tb["name"],
                            tool_result=result.output,
                            tool_error=result.error,
                            tool_use_id=tb["id"],
                        )

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tb["id"],
                            "content": result.output,
                        })

        finally:
            # Persist messages even if cancelled — use context's set_state
            ctx.set_state("messages", messages)

    async def _check_permission(self, tool_name: str, params: dict[str, Any]) -> PermissionDecision:
        """Check permission, invoking callback for ASK decisions."""
        decision = self._context.permissions.check(tool_name, params)
        if decision == PermissionDecision.ASK:
            if self._permission_callback is not None:
                decision = await self._permission_callback(tool_name, params)
            else:
                # No callback (print mode) — deny by default
                decision = PermissionDecision.DENY
        return decision

    async def _execute_tool(self, name: str, params: dict[str, Any]) -> ToolResult:
        """Execute a tool by name using the safe wrapper with retry protection."""
        from .retry import with_retry, RetryPolicy, QueryType

        tool = self._context.registry.get(name)
        if not tool:
            return ToolResult(output=f"Unknown tool: {name}", error=True)

        # Wrap tool execution with retry protection
        # 3 attempts for transient errors (5xx, connection errors)
        return await with_retry(
            operation=tool.execute_safe,
            policy=RetryPolicy(
                DELAY_MULTIPLIERS=[1.0, 2.0, 4.0],
                RESPECT_RETRY_AFTER=True,
                MAX_RETRIES=3,
            ),
            query_type=QueryType.FOREGROUND,
            description=f"Execute tool: {name}",
        )(params)
