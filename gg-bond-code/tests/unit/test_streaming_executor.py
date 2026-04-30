"""Tests for tools/streaming_executor.py — concurrent tool execution and partitioning."""

import asyncio

from gg_bond_code.tools.base import Tool, ToolRegistry, ToolResult
from gg_bond_code.tools.streaming_executor import (
    StreamingToolExecutor,
    ToolExecution,
    Batch,
    partition_tool_calls,
)


# --- Test tools ---

class FakeReadTool(Tool):
    name = "Read"
    description = "test read"

    def get_schema(self):
        return {"type": "object", "properties": {}}

    def is_concurrency_safe(self, params):
        return True

    async def execute(self, params):
        await asyncio.sleep(0.01)
        return ToolResult(output="read result")


class FakeGrepTool(Tool):
    name = "Grep"
    description = "test grep"

    def get_schema(self):
        return {"type": "object", "properties": {}}

    def is_concurrency_safe(self, params):
        return True

    async def execute(self, params):
        await asyncio.sleep(0.01)
        return ToolResult(output="grep result")


class FakeEditTool(Tool):
    name = "Edit"
    description = "test edit"

    def get_schema(self):
        return {"type": "object", "properties": {}}

    def is_concurrency_safe(self, params):
        return False

    async def execute(self, params):
        await asyncio.sleep(0.01)
        return ToolResult(output="edit result")


class FakeBashTool(Tool):
    name = "Bash"
    description = "test bash"

    def get_schema(self):
        return {"type": "object", "properties": {}}

    def is_concurrency_safe(self, params):
        return False

    async def execute(self, params):
        await asyncio.sleep(0.01)
        return ToolResult(output="bash result")


class SlowReadTool(Tool):
    name = "SlowRead"
    description = "slow read"

    def get_schema(self):
        return {"type": "object", "properties": {}}

    def is_concurrency_safe(self, params):
        return True

    async def execute(self, params):
        await asyncio.sleep(0.5)
        return ToolResult(output="slow read result")


class ErrorTool(Tool):
    name = "ErrorTool"
    description = "error tool"

    def get_schema(self):
        return {"type": "object", "properties": {}}

    def is_concurrency_safe(self, params):
        return True

    async def execute(self, params):
        raise RuntimeError("tool crashed")


def _make_registry(*tools):
    registry = ToolRegistry()
    for t in tools:
        registry.register(t())
    return registry


# --- Partition tests ---

def test_partition_all_concurrent():
    """All concurrent-safe tools form a single batch."""
    calls = [
        ToolExecution(tool_use_id="1", tool_name="Read", input={}, is_concurrency_safe=True),
        ToolExecution(tool_use_id="2", tool_name="Grep", input={}, is_concurrency_safe=True),
        ToolExecution(tool_use_id="3", tool_name="Read", input={}, is_concurrency_safe=True),
    ]
    batches = partition_tool_calls(calls)
    assert len(batches) == 1
    assert batches[0].concurrent is True
    assert len(batches[0].tools) == 3


def test_partition_all_serial():
    """All non-concurrent tools each get their own batch."""
    calls = [
        ToolExecution(tool_use_id="1", tool_name="Edit", input={}, is_concurrency_safe=False),
        ToolExecution(tool_use_id="2", tool_name="Bash", input={}, is_concurrency_safe=False),
    ]
    batches = partition_tool_calls(calls)
    assert len(batches) == 2
    assert all(not b.concurrent for b in batches)
    assert all(len(b.tools) == 1 for b in batches)


def test_partition_mixed():
    """Mixed tools are partitioned correctly.

    [Read, Grep, Read, Edit, Grep, Read]
     └── concurrent ──┘  └─ serial ─┘  └── concurrent ──┘
    """
    calls = [
        ToolExecution(tool_use_id="1", tool_name="Read", input={}, is_concurrency_safe=True),
        ToolExecution(tool_use_id="2", tool_name="Grep", input={}, is_concurrency_safe=True),
        ToolExecution(tool_use_id="3", tool_name="Read", input={}, is_concurrency_safe=True),
        ToolExecution(tool_use_id="4", tool_name="Edit", input={}, is_concurrency_safe=False),
        ToolExecution(tool_use_id="5", tool_name="Grep", input={}, is_concurrency_safe=True),
        ToolExecution(tool_use_id="6", tool_name="Read", input={}, is_concurrency_safe=True),
    ]
    batches = partition_tool_calls(calls)
    assert len(batches) == 3
    # Batch 1: concurrent (3 tools)
    assert batches[0].concurrent is True
    assert len(batches[0].tools) == 3
    # Batch 2: serial (1 tool)
    assert batches[1].concurrent is False
    assert len(batches[1].tools) == 1
    # Batch 3: concurrent (2 tools)
    assert batches[2].concurrent is True
    assert len(batches[2].tools) == 2


def test_partition_empty():
    """Empty list produces no batches."""
    batches = partition_tool_calls([])
    assert len(batches) == 0


def test_partition_consecutive_serial():
    """Consecutive serial tools each get their own batch."""
    calls = [
        ToolExecution(tool_use_id="1", tool_name="Edit", input={}, is_concurrency_safe=False),
        ToolExecution(tool_use_id="2", tool_name="Edit", input={}, is_concurrency_safe=False),
    ]
    batches = partition_tool_calls(calls)
    assert len(batches) == 2
    assert all(not b.concurrent for b in batches)


# --- StreamingToolExecutor tests ---

def test_execute_concurrent_tools():
    """Concurrent-safe tools execute in parallel."""
    registry = _make_registry(FakeReadTool, FakeGrepTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)

    async def run():
        await executor.add_tool({"id": "1", "name": "Read", "input": {}})
        await executor.add_tool({"id": "2", "name": "Grep", "input": {}})
        await executor.add_tool({"id": "3", "name": "Read", "input": {}})
        results = await executor.execute_all()
        return results

    results = asyncio.run(run())
    assert len(results) == 3
    assert all(r.result is not None for r in results)
    assert all(not r.result.error for r in results)


def test_execute_serial_tools():
    """Non-concurrent tools execute one at a time."""
    registry = _make_registry(FakeEditTool, FakeBashTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)

    async def run():
        await executor.add_tool({"id": "1", "name": "Edit", "input": {}})
        await executor.add_tool({"id": "2", "name": "Bash", "input": {}})
        results = await executor.execute_all()
        return results

    results = asyncio.run(run())
    assert len(results) == 2
    assert all(r.result is not None for r in results)


def test_execute_mixed_tools():
    """Mixed concurrent and serial tools execute correctly."""
    registry = _make_registry(FakeReadTool, FakeGrepTool, FakeEditTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)

    async def run():
        await executor.add_tool({"id": "1", "name": "Read", "input": {}})
        await executor.add_tool({"id": "2", "name": "Grep", "input": {}})
        await executor.add_tool({"id": "3", "name": "Edit", "input": {}})
        results = await executor.execute_all()
        return results

    results = asyncio.run(run())
    assert len(results) == 3
    assert all(r.result is not None for r in results)


def test_concurrent_execution_is_faster():
    """Concurrent tools execute faster than serial due to parallelism."""
    registry = _make_registry(SlowReadTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)

    async def run():
        await executor.add_tool({"id": "1", "name": "SlowRead", "input": {}})
        await executor.add_tool({"id": "2", "name": "SlowRead", "input": {}})
        await executor.add_tool({"id": "3", "name": "SlowRead", "input": {}})
        results = await executor.execute_all()
        return results

    import time
    start = time.monotonic()
    results = asyncio.run(run())
    elapsed = time.monotonic() - start

    # 3 tasks at 0.5s each, concurrent should be ~0.5s not ~1.5s
    assert len(results) == 3
    assert elapsed < 1.2  # generous margin, but must be < 1.5s serial


def test_get_completed_results():
    """get_completed_results returns formatted results after execution."""
    registry = _make_registry(FakeReadTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)

    async def run():
        await executor.add_tool({"id": "call_1", "name": "Read", "input": {"file_path": "/tmp/a"}})
        await executor.execute_all()
        return executor.get_completed_results()

    results = asyncio.run(run())
    assert len(results) == 1
    assert results[0]["id"] == "call_1"
    assert results[0]["name"] == "Read"
    assert results[0]["output"] == "read result"
    assert results[0]["error"] is False


def test_discard_prevents_execution():
    """discard() prevents any further execution and results."""
    registry = _make_registry(FakeReadTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)

    async def run():
        await executor.add_tool({"id": "1", "name": "Read", "input": {}})
        executor.discard()
        results = await executor.execute_all()
        return results

    results = asyncio.run(run())
    assert len(results) == 0
    assert executor.get_completed_results() == []


def test_discard_returns_empty_results():
    """After discard, get_completed_results returns empty list."""
    registry = _make_registry(FakeReadTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)

    async def run():
        await executor.add_tool({"id": "1", "name": "Read", "input": {}})
        await executor.execute_all()
        # Results exist before discard
        assert len(executor.get_completed_results()) == 1
        executor.discard()
        # After discard, results are cleared
        return executor.get_completed_results()

    results = asyncio.run(run())
    assert len(results) == 0


def test_add_tool_after_discard():
    """Adding tools after discard is a no-op."""
    registry = _make_registry(FakeReadTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)

    async def run():
        executor.discard()
        await executor.add_tool({"id": "1", "name": "Read", "input": {}})
        results = await executor.execute_all()
        return results

    results = asyncio.run(run())
    assert len(results) == 0


def test_unknown_tool_returns_error():
    """Unknown tool name returns an error result."""
    registry = _make_registry(FakeReadTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)

    async def run():
        await executor.add_tool({"id": "1", "name": "NonExistent", "input": {}})
        results = await executor.execute_all()
        return results

    results = asyncio.run(run())
    assert len(results) == 1
    assert results[0].result.error is True
    assert "Unknown tool" in results[0].result.output


def test_tool_error_is_caught():
    """Tool that raises an exception returns error result."""
    registry = _make_registry(ErrorTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)

    async def run():
        await executor.add_tool({"id": "1", "name": "ErrorTool", "input": {}})
        results = await executor.execute_all()
        return results

    results = asyncio.run(run())
    assert len(results) == 1
    assert results[0].result.error is True
    assert "tool crashed" in results[0].result.output


def test_get_remaining_results():
    """get_remaining_results executes pending tools and returns results."""
    registry = _make_registry(FakeReadTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)

    async def run():
        await executor.add_tool({"id": "1", "name": "Read", "input": {}})
        results = await executor.get_remaining_results()
        return results

    results = asyncio.run(run())
    assert len(results) == 1
    assert results[0]["name"] == "Read"


def test_is_discarded_property():
    """is_discarded property reflects discard state."""
    registry = _make_registry(FakeReadTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)
    assert executor.is_discarded is False
    executor.discard()
    assert executor.is_discarded is True


def test_pending_and_completed_counts():
    """pending_count and completed_count track state correctly."""
    registry = _make_registry(FakeReadTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=10)

    async def run():
        assert executor.pending_count == 0
        assert executor.completed_count == 0

        await executor.add_tool({"id": "1", "name": "Read", "input": {}})
        assert executor.pending_count == 1

        await executor.execute_all()
        assert executor.completed_count == 1

    asyncio.run(run())


def test_concurrency_limit():
    """Semaphore limits concurrent execution to max_concurrent."""
    registry = _make_registry(SlowReadTool)
    executor = StreamingToolExecutor(registry=registry, max_concurrent=2)

    async def run():
        for i in range(5):
            await executor.add_tool({"id": str(i), "name": "SlowRead", "input": {}})
        results = await executor.execute_all()
        return results

    results = asyncio.run(run())
    assert len(results) == 5
    assert all(r.result is not None for r in results)
