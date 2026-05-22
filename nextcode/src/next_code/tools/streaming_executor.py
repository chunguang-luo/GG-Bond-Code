"""Streaming tool executor — execute tools during API streaming.

Mirrors the StreamingToolExecutor pattern from Claude Code's
services/tools/StreamingToolExecutor.ts, with concurrency-safe partitioning
from toolOrchestration.ts.

Key design:
- Tools are added to pending list as tool_use blocks arrive during streaming
- Concurrent-safe tools (Read, Glob, Grep) run in parallel (up to max_concurrent)
- Non-concurrent tools (Edit, Write, Bash) run serially
- discard() cancels all pending/running tools (used on streaming fallback)
- Results are collected via get_completed_results() / get_remaining_results()
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from typing import Any

from .base import ToolRegistry, ToolResult, _current_context

# Permission decision constants
PD_ALLOW = "allow"
PD_DENY = "deny"


@dataclass
class ToolExecution:
    """Tracks a single tool execution."""

    tool_use_id: str
    tool_name: str
    input: dict[str, Any]
    result: ToolResult | None = None
    task: asyncio.Task | None = None
    is_concurrency_safe: bool = False


@dataclass
class Batch:
    """A batch of tool calls that share the same concurrency mode."""

    tools: list[ToolExecution] = field(default_factory=list)
    concurrent: bool = True


def partition_tool_calls(
    tool_calls: list[ToolExecution],
) -> list[Batch]:
    """Partition tool calls into concurrent and serial batches.

    Consecutive concurrency-safe tools are grouped into one concurrent batch.
    Non-concurrent tools each get their own serial batch.

    Example:
        [Grep, Glob, FileRead, FileEdit, Grep, FileRead]
         └── concurrent ──┘  └─ serial ─┘  └── concurrent ──┘
    """
    batches: list[Batch] = []

    for tc in tool_calls:
        if tc.is_concurrency_safe:
            # Append to last batch if it's concurrent, else start new one
            if batches and batches[-1].concurrent:
                batches[-1].tools.append(tc)
            else:
                batches.append(Batch(tools=[tc], concurrent=True))
        else:
            # Non-concurrent tools always get their own serial batch
            batches.append(Batch(tools=[tc], concurrent=False))

    return batches


class StreamingToolExecutor:
    """Execute tools concurrently where safe, queue during streaming."""

    def __init__(
        self,
        registry: ToolRegistry,
        max_concurrent: int = 10,
        context: Any | None = None,
    ) -> None:
        self.registry = registry
        self.max_concurrent = max_concurrent
        self._context = context
        self._pending: list[ToolExecution] = []
        self._completed: list[ToolExecution] = []
        self._discarded: bool = False
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running_tasks: set[asyncio.Task] = set()

    async def add_tool(self, tool_block: dict[str, Any]) -> None:
        """Add a tool_use block to the execution queue.

        Called during streaming as each tool_use block arrives.

        Deduplication: If the tool is an Agent call, skip it when an Agent
        of the same subagent_type is already pending or completed. This
        prevents the model from spawning duplicate Agents in a single turn.
        """
        if self._discarded:
            return

        tool_name = tool_block.get("name", "")
        tool = self.registry.get(tool_name)
        is_safe = tool.is_concurrency_safe(tool_block.get("input", {})) if tool else False

        # Deduplicate Agent calls: same subagent_type only once per turn
        if tool_name == "Agent":
            subagent_type = tool_block.get("input", {}).get("subagent_type", "general-purpose")
            existing_types = set()
            for ex in self._pending:
                if ex.tool_name == "Agent":
                    existing_types.add(ex.input.get("subagent_type", "general-purpose"))
            for ex in self._completed:
                if ex.tool_name == "Agent":
                    existing_types.add(ex.input.get("subagent_type", "general-purpose"))
            if subagent_type in existing_types:
                logger.warning(
                    "Skipping duplicate Agent call: subagent_type=%s already queued/completed",
                    subagent_type,
                )
                return

        execution = ToolExecution(
            tool_use_id=tool_block.get("id", ""),
            tool_name=tool_name,
            input=tool_block.get("input", {}),
            is_concurrency_safe=is_safe,
        )
        self._pending.append(execution)

    async def execute_all(
        self,
        permission_checker: Callable[[str, dict[str, Any]], Awaitable[str]] | None = None,
    ) -> list[ToolExecution]:
        """Execute all queued tools respecting concurrency rules.

        Partitions pending tools into batches, then runs each batch:
        - Concurrent batches: tools run in parallel (up to max_concurrent)
        - Serial batches: tools run one at a time

        Args:
            permission_checker: Optional callback that returns PD_ALLOW or PD_DENY
                for each tool before execution. DENY tools get an error result
                and are skipped without execution.

        Returns all completed executions.
        """
        if self._discarded:
            return []

        # Drain queue into pending list (already there via add_tool)
        pending = list(self._pending)
        self._pending.clear()

        # Check permissions before execution
        if permission_checker is not None:
            filtered: list[ToolExecution] = []
            for tc in pending:
                decision = await permission_checker(tc.tool_name, tc.input)
                if decision == PD_DENY:
                    tc.result = ToolResult(output="Permission denied", error=True)
                    self._completed.append(tc)
                else:
                    filtered.append(tc)
            pending = filtered

        # Partition into batches
        batches = partition_tool_calls(pending)

        for batch in batches:
            if self._discarded:
                break

            if batch.concurrent:
                # Run all tools in this batch concurrently
                tasks = []
                for tc in batch.tools:
                    task = asyncio.create_task(self._run_with_semaphore(tc))
                    tc.task = task
                    self._running_tasks.add(task)
                    tasks.append(task)

                # Wait for all tasks in this batch
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for tc, result in zip(batch.tools, results):
                    if isinstance(result, Exception):
                        tc.result = ToolResult(output=f"Execution error: {result}", error=True)
                    self._completed.append(tc)
                    self._running_tasks.discard(tc.task)
            else:
                # Run tools serially
                for tc in batch.tools:
                    if self._discarded:
                        break
                    tc.result = await self._execute_single(tc)
                    self._completed.append(tc)

        return list(self._completed)

    async def _run_with_semaphore(self, execution: ToolExecution) -> ToolExecution:
        """Run a single tool with semaphore-based concurrency limit."""
        async with self._semaphore:
            if self._discarded:
                execution.result = ToolResult(output="Discarded", error=True)
                return execution
            execution.result = await self._execute_single(execution)
            return execution

    async def _execute_single(self, execution: ToolExecution) -> ToolResult:
        """Execute a single tool safely."""
        if self._discarded:
            return ToolResult(output="Discarded", error=True)

        tool = self.registry.get(execution.tool_name)
        if not tool:
            return ToolResult(output=f"Unknown tool: {execution.tool_name}", error=True)

        # Inject context via contextvars for tools that need it
        token = None
        if self._context is not None:
            token = _current_context.set(self._context)
        try:
            return await tool.execute_safe(execution.input)
        finally:
            if token is not None:
                _current_context.reset(token)

    def get_completed_results(self) -> list[dict[str, Any]]:
        """Get results of completed tool executions.

        Returns list of dicts with keys: id, name, output, error.
        """
        if self._discarded:
            return []

        results = []
        for tc in self._completed:
            if tc.result is not None:
                results.append({
                    "id": tc.tool_use_id,
                    "name": tc.tool_name,
                    "output": tc.result.output,
                    "error": tc.result.error,
                    "metadata": tc.result.metadata,
                })
        return results

    async def get_remaining_results(self) -> list[dict[str, Any]]:
        """Get results for tools still in the queue (blocking until done).

        Used when streaming ends and we need to collect all remaining results.
        """
        if self._discarded:
            return []

        # Execute any remaining pending tools
        if self._pending:
            await self.execute_all()

        return self.get_completed_results()

    def discard(self) -> None:
        """Discard all queued and running tools.

        Called on streaming fallback or model switch.
        After discard, get_completed_results() and get_remaining_results()
        return empty lists. Running tasks will check _discarded and abort.
        """
        self._discarded = True

        # Cancel all running tasks
        for task in self._running_tasks:
            task.cancel()

        # Clear all state
        self._pending.clear()
        self._completed.clear()

    @property
    def is_discarded(self) -> bool:
        return self._discarded

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def completed_count(self) -> int:
        return len(self._completed)
