"""Query runner — the core conversation loop, mirrors query.ts."""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Awaitable

from .api.client import stream_message, get_model_family
from .api.recovery import MaxOutputTokensRecovery, SurfaceErrorRecovery
from .prompts.system import build_system_prompt
from .state.context import ToolUseContext, create_store_context
from .state.store import Store
from .state.transition import LoopState, TransitionReason
from .tools.base import ToolRegistry, ToolResult, create_default_registry, _current_context
from .tools.streaming_executor import StreamingToolExecutor, PD_DENY
from .permissions.manager import PermissionManager, PermissionDecision
from .context.system import get_system_context, format_system_context, clear_system_context_cache
from .context.user import get_user_context, prepend_user_context
from .compact.strategy import should_compact_messages, MessageCountStrategy
from .compact.warning import CompactWarningManager
from .compact.manager import CompactManager, CompactLevel
from .compact.budget import estimate_token_count


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
    metadata: dict[str, Any] = field(default_factory=dict)  # Extra data (e.g. warning level)
    source: str = ""  # "agent" for sub-agent events, "" for parent events


class QueryRunner:
    """Drive the conversation loop: user message → API → tool execution → repeat."""

    def __init__(
        self,
        model: str | None = None,
        tool_registry: ToolRegistry | None = None,
        max_turns: int = 50,
        permission_callback: Callable[[str, dict[str, Any]], Awaitable[PermissionDecision]] | None = None,
        context: ToolUseContext | None = None,
        enable_compaction: bool = True,
        max_messages: int = 20,
        enable_streaming_tools: bool = False,
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
        self.family = get_model_family(self.model) or "openai"
        self.max_turns = max_turns
        self.system_prompt = build_system_prompt(cwd=ctx.get_state("cwd"))
        self._permission_callback = permission_callback
        self._enable_compaction = enable_compaction
        self._max_messages = max_messages
        self._recovery_strategies = [
            MaxOutputTokensRecovery(max_recovery_count=3),
            SurfaceErrorRecovery(),
        ]
        self._recovery_count = 0
        self._enable_streaming_tools = enable_streaming_tools
        self._compact_manager = CompactManager(model=self.model, file_cache=self._context.file_cache)
        self._streaming_executor: StreamingToolExecutor | None = None
        self._loop_state = LoopState()
        self._warning_manager = CompactWarningManager()

    @property
    def permissions(self) -> PermissionManager:
        return self._context.permissions

    @permissions.setter
    def permissions(self, value: PermissionManager) -> None:
        self._context.permissions = value

    @property
    def registry(self) -> ToolRegistry:
        return self._context.registry

    @property
    def loop_state(self) -> LoopState:
        """Return the current loop state (transition log, turn count, etc.)."""
        return self._loop_state

    @staticmethod
    def _format_tool_labels(tool_blocks: list[dict[str, Any]]) -> str:
        """Format tool names with key params for transition log detail."""
        priority_keys = ["file_path", "path", "command", "pattern", "query", "url", "name"]
        labels = []
        for tb in tool_blocks:
            name = tb.get("name", "?")
            inp = tb.get("input", {})
            if isinstance(inp, dict):
                for key in priority_keys:
                    if key in inp:
                        val = str(inp[key])
                        labels.append(f"{name}({val})")
                        break
                else:
                    labels.append(name)
            else:
                labels.append(name)
        return ", ".join(labels)

    async def run(self, user_message: str) -> AsyncIterator[QueryEvent]:
        """Run a single user message through the conversation loop."""
        ctx = self._context
        self._loop_state.reset()
        self._loop_state.set_transition(TransitionReason.NEXT_TURN, detail="run started")
        # Clear warning suppression from previous turns
        self._warning_manager.clear_suppression()

        # Set up event forwarding so sub-tools (e.g. AgentTool) can emit events
        # that flow through this yield loop to IPCBridge and the frontend.
        ctx._event_queue: asyncio.Queue[QueryEvent | None] = asyncio.Queue()
        ctx.emit_event = lambda event: ctx._event_queue.put_nowait(event)

        # Get contexts (computed on demand with caching)
        system_ctx = get_system_context(ctx.get_state("cwd"))
        user_ctx = get_user_context(ctx.get_state("cwd"))

        # Build full system prompt
        static_sections = build_system_prompt(cwd=ctx.get_state("cwd"))
        # Omit git status context for agents that don't need it
        omit_git = ctx.get_state("omit_git_status") or False
        dynamic_sections = (
            [format_system_context(system_ctx)] if system_ctx and not omit_git else []
        )

        full_system_prompt = [
            *static_sections,
            *dynamic_sections,
        ]

        # Prepare messages with user context
        messages: list[dict[str, Any]] = ctx.get_state("messages") or []
        # Inject critical reminder before user message if set (e.g. Verification Agent)
        if getattr(ctx, "critical_reminder", None):
            messages.append({
                "role": "user",
                "content": f"[system] {ctx.critical_reminder}",
            })
            messages.append({"role": "assistant", "content": "Understood."})
        messages.append({"role": "user", "content": user_message})
        # Inject user context — omit NEXTCODE.md for agents that don't need it
        omit_nextcode_md = ctx.get_state("omit_nextcode_md") or False
        if omit_nextcode_md:
            # Keep date but drop NEXTCODE.md
            filtered_ctx = {k: v for k, v in user_ctx.items() if k != "nextcode_md"}
            messages = prepend_user_context(messages, filtered_ctx)
        else:
            messages = prepend_user_context(messages, user_ctx)

        # Check if compaction is needed before starting
        if self._enable_compaction:
            token_usage = self._compact_manager.get_token_usage(messages)
            level, warning_state = self._compact_manager.evaluate(token_usage)

            if level == CompactLevel.BLOCKING:
                self._loop_state.set_transition(
                    TransitionReason.COMPACT_BLOCKING,
                    detail=f"token usage: {token_usage}",
                )
                yield QueryEvent(
                    type="error",
                    content="Context window full. Use /compact to manually compress the conversation.",
                )
                return

            if level in (CompactLevel.MICRO, CompactLevel.FULL):
                compacted_messages, reason = await self._compact_manager.execute(level, messages)
                messages = compacted_messages
                transition_reason = (
                    TransitionReason.COMPACT_MICRO
                    if level == CompactLevel.MICRO
                    else TransitionReason.COMPACT_FULL
                )
                self._loop_state.set_transition(transition_reason, detail=reason)
                yield QueryEvent(type="thinking", content=f"[Context compaction: {reason}]")

                # Warning suppression: suppress after FULL, clear after MICRO
                if level == CompactLevel.FULL:
                    self._warning_manager.suppress()
                elif level == CompactLevel.MICRO:
                    self._warning_manager.clear_suppression()

        tools = ctx.registry.to_api_format(self.family)

        try:
            for _ in range(self.max_turns):
                self._loop_state.turn_count += 1
                text_parts: list[str] = []
                thinking_parts: list[str] = []
                tool_use_blocks: list[dict[str, Any]] = []

                # Create a fresh streaming executor for this turn if enabled
                if self._enable_streaming_tools:
                    self._streaming_executor = StreamingToolExecutor(
                        registry=ctx.registry,
                        max_concurrent=10,
                        context=self._context,
                    )

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
                            tool_block = {
                                "id": evt["id"],
                                "name": evt["name"],
                                "input": evt["input"],
                            }
                            tool_use_blocks.append(tool_block)
                            # Use recent thinking parts for tool_purpose context
                            recent_thinking = "".join(thinking_parts[-3:]) if thinking_parts else ""
                            yield QueryEvent(
                                type="tool_use",
                                content=f"Using tool: {evt['name']}",
                                tool_name=evt["name"],
                                tool_input=evt["input"],
                                tool_use_id=evt["id"],
                                tool_purpose=("".join(text_parts).strip() or recent_thinking.strip()),
                            )
                            # Queue tool for streaming execution
                            if self._streaming_executor is not None:
                                await self._streaming_executor.add_tool(tool_block)
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
                            # Queue tool for streaming execution
                            if self._streaming_executor is not None:
                                await self._streaming_executor.add_tool(evt)
                except Exception as e:
                    # Discard streaming executor on error
                    if self._streaming_executor is not None:
                        self._streaming_executor.discard()
                        self._streaming_executor = None
                        self._loop_state.set_transition(TransitionReason.STREAMING_DISCARD)

                    # Try recovery strategies
                    recovered = False
                    for recovery_strategy in self._recovery_strategies:
                        if await recovery_strategy.should_recover(e, self._recovery_count):
                            recovery_message = await recovery_strategy.recover(messages, e)
                            messages.append(recovery_message)
                            self._recovery_count += 1
                            recovered = True
                            self._loop_state.set_transition(
                                TransitionReason.RECOVERY_INJECTED,
                                detail=recovery_message.get("content", "")[:80],
                            )
                            yield QueryEvent(
                                type="thinking",
                                content=f"[Recovery: {recovery_message['content']}]",
                            )
                            break

                    if not recovered:
                        tb = traceback.format_exc()
                        self._loop_state.set_transition(TransitionReason.SURFACE_ERROR, detail=str(e)[:80])
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

                # If no tool use, the model is about to finish.
                # Before exiting, wait for any background tasks and inject results.
                if not has_tool_use:
                    pending = await self._await_background_tasks()
                    if pending:
                        # Inject background task results so the model can
                        # summarize or take follow-up action before finishing.
                        messages.append({"role": "user", "content": pending})
                        yield QueryEvent(type="thinking", content="[等待后台任务完成，继续生成]")
                        # Don't count this as a new turn - just continue waiting
                        continue
                    self._loop_state.set_transition(TransitionReason.DONE, detail="no tool use")
                    break

                # Execute tools — streaming or serial mode
                if self._streaming_executor is not None:
                    # Streaming mode: execute all queued tools with concurrency partitioning
                    # and permission checks
                    async def _perm_check(name: str, params: dict[str, Any]) -> str:
                        decision = await self._check_permission(name, params)
                        return "deny" if decision == PermissionDecision.DENY else "allow"

                    try:
                        await self._streaming_executor.execute_all(permission_checker=_perm_check)
                        completed = self._streaming_executor.get_completed_results()
                    finally:
                        # Always clean up executor, even on exception
                        self._streaming_executor.discard()
                        self._streaming_executor = None

                    for result in completed:
                        async for event in self._yield_and_append_tool_result(result, messages):
                            yield event

                    self._loop_state.set_transition(TransitionReason.TOOL_COMPLETED, detail=self._format_tool_labels(tool_use_blocks))
                else:
                    # Serial mode: execute tools one by one with permission checks
                    # Deduplicate Agent calls: same subagent_type only once per turn
                    seen_agent_types: set[str] = set()
                    for tb in tool_use_blocks:
                        if tb.get("name") == "Agent":
                            sub_type = tb.get("input", {}).get("subagent_type", "general-purpose")
                            if sub_type in seen_agent_types:
                                logger.warning("Skipping duplicate Agent: subagent_type=%s", sub_type)
                                continue
                            seen_agent_types.add(sub_type)
                        async for event in self._execute_serial_tool(tb, messages):
                            yield event

                    self._loop_state.set_transition(TransitionReason.TOOL_COMPLETED, detail=self._format_tool_labels(tool_use_blocks))

        finally:
            # Persist messages even if cancelled — use context's set_state
            ctx.set_state("messages", messages)

        # Emit warning event after the conversation loop completes
        if self._enable_compaction:
            token_usage = self._compact_manager.get_token_usage(messages)
            warning = self._warning_manager.evaluate(token_usage, self.model)
            if warning.level != "ok" and not self._warning_manager.is_suppressed:
                yield QueryEvent(
                    type="warning",
                    content=warning.message,
                    metadata={
                        "level": warning.level,
                        "percent_used": warning.percent_used,
                        "token_usage": warning.token_usage,
                        "effective_window": warning.effective_window,
                    },
                )

    async def _await_background_tasks(self) -> str:
        """Wait for running background tasks and return a summary of their results.

        Returns an empty string if no tasks are running, or a formatted summary
        of completed task results to inject as a user message.
        """
        from .tasks.registry import get_task_registry
        from .tasks.types import TaskType, TaskStatus

        registry = get_task_registry()
        running = registry.list_running()
        if not running:
            return ""

        # Collect task IDs to track after waiting
        running_ids = {task.id for task in running}

        # Collect asyncio tasks to wait for
        aio_tasks = []
        for task in running:
            if task.asyncio_task is not None:
                aio_tasks.append(task.asyncio_task)

        if aio_tasks:
            # Wait for all background tasks to complete (max 10 minutes)
            try:
                await asyncio.wait(aio_tasks, timeout=600)
            except asyncio.TimeoutError:
                self._loop_state.set_transition(
                    TransitionReason.SURFACE_ERROR,
                    detail="Background tasks wait timeout (10 min)",
                )
            except Exception as e:
                self._loop_state.set_transition(
                    TransitionReason.SURFACE_ERROR,
                    detail=f"Background tasks wait error: {e}",
                )

        # Collect results from tasks we were waiting for
        parts: list[str] = []
        for task in registry.list_all():
            # Only collect tasks we were tracking
            if task.id not in running_ids:
                continue

            if not task.is_terminal():
                parts.append(f"- 任务 {task.id} ({task.type.value}): 仍在运行中")
                continue

            type_label = "Agent" if task.type == TaskType.LOCAL_AGENT else "Shell"
            status = "完成" if task.status == TaskStatus.COMPLETED else f"失败({task.status.value})"
            desc = task.description or (task.command[:80] if hasattr(task, 'command') and task.command else "")
            result = (task.result or "")[:2000]
            parts.append(f"- {type_label} {task.id} ({desc}): {status}\n{result}")

        if not parts:
            return ""

        return "[后台任务结果]\n" + "\n\n".join(parts) + "\n\n请基于以上后台任务结果继续回答用户的问题。"

    async def _check_permission(self, tool_name: str, params: dict[str, Any]) -> PermissionDecision:
        """Check permission, invoking callback for ASK decisions."""
        decision = self._context.permissions.check(
            tool_name, params, registry=self._context.registry,
        )
        if decision == PermissionDecision.ASK:
            if self._permission_callback is not None:
                decision = await self._permission_callback(tool_name, params)
            else:
                # No callback (print mode) — deny by default
                decision = PermissionDecision.DENY
        return decision

    async def _execute_tool(self, name: str, params: dict[str, Any]) -> ToolResult:
        """Execute a tool by name using the safe wrapper with retry protection."""
        from .api.retry import with_retry, RetryPolicy, QueryType

        tool = self._context.registry.get(name)
        if not tool:
            return ToolResult(output=f"Unknown tool: {name}", error=True)

        # Inject context via contextvars so tools can access FileStateCache
        token = _current_context.set(self._context)
        try:
            # Wrap tool execution with retry protection
            # Exponential backoff: 1s, 2s, 4s, then capped at last multiplier
            return await with_retry(
                tool.execute_safe,
                params,
                policy=RetryPolicy(
                    delay_multipliers=[1, 2, 4],
                    respect_retry_after=True,
                    max_retries=10,
                ),
                query_type=QueryType.FOREGROUND,
                description=f"Execute tool: {name}",
            )
        finally:
            _current_context.reset(token)

    async def _yield_and_append_tool_result(
        self,
        result: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> None:
        """Yield a tool_result event and append the formatted message to history."""
        yield QueryEvent(
            type="tool_result",
            tool_name=result["name"],
            tool_result=result["output"],
            tool_error=result["error"],
            tool_use_id=result["id"],
            metadata=result.get("metadata", {}),
        )

        if self.family == "anthropic":
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": result["id"],
                    "content": result["output"],
                    "is_error": result["error"],
                }],
            })
        else:
            messages.append({
                "role": "tool",
                "tool_call_id": result["id"],
                "content": result["output"],
            })

    async def _execute_serial_tool(
        self,
        tb: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[QueryEvent]:
        """Execute a single tool in serial mode and yield events."""
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

        result_dict = {
            "id": tb["id"],
            "name": tb["name"],
            "output": result.output,
            "error": result.error,
            "metadata": result.metadata,
        }
        async for event in self._yield_and_append_tool_result(result_dict, messages):
            yield event
