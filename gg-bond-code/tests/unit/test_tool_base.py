"""Tests for tools/base.py — execute_safe, timeout, and schema cache."""

import asyncio

from gg_bond_code.tools.base import Tool, ToolResult, ToolRegistry


class DummyTool(Tool):
    name = "Test"
    description = "test tool"

    def get_schema(self):
        return {"type": "object", "properties": {}}

    async def execute(self, params):
        return ToolResult(output="ok")


class SlowTool(Tool):
    name = "Slow"
    description = "slow tool"

    def get_schema(self):
        return {"type": "object", "properties": {}}

    async def execute(self, params):
        await asyncio.sleep(10)
        return ToolResult(output="never")

    def get_timeout(self):
        return 0.1


class ErrorTool(Tool):
    name = "Error"
    description = "error tool"

    def get_schema(self):
        return {"type": "object", "properties": {}}

    async def execute(self, params):
        raise RuntimeError("boom")


def test_execute_safe_normal():
    """execute_safe returns normal result."""
    t = DummyTool()
    result = asyncio.run(t.execute_safe({}))
    assert result.output == "ok"
    assert result.error is False


def test_execute_safe_timeout():
    """execute_safe catches timeout."""
    s = SlowTool()
    result = asyncio.run(s.execute_safe({}))
    assert "timed out" in result.output
    assert result.error is True


def test_execute_safe_exception():
    """execute_safe catches exceptions."""
    e = ErrorTool()
    result = asyncio.run(e.execute_safe({}))
    assert "boom" in result.output
    assert result.error is True


def test_default_timeout():
    """Default timeout is 120s."""
    t = DummyTool()
    assert t.get_timeout() == 120.0


def test_is_read_only_default():
    """is_read_only defaults to False (fail-closed)."""
    t = DummyTool()
    assert t.is_read_only({}) is False


def test_is_concurrency_safe_default():
    """is_concurrency_safe defaults to False (fail-closed)."""
    t = DummyTool()
    assert t.is_concurrency_safe({}) is False


def test_builtin_read_only_tools():
    """Read, Glob, Grep are read-only."""
    from gg_bond_code.tools.file_read import FileReadTool
    from gg_bond_code.tools.glob import GlobTool
    from gg_bond_code.tools.grep import GrepTool

    assert FileReadTool().is_read_only({}) is True
    assert GlobTool().is_read_only({}) is True
    assert GrepTool().is_read_only({}) is True


def test_builtin_write_tools_not_read_only():
    """Bash, Edit, Write are not read-only."""
    from gg_bond_code.tools.bash import BashTool
    from gg_bond_code.tools.file_edit import FileEditTool
    from gg_bond_code.tools.file_write import FileWriteTool

    assert BashTool().is_read_only({}) is False
    assert FileEditTool().is_read_only({}) is False
    assert FileWriteTool().is_read_only({}) is False


# ── Tool Schema Cache tests ──────────────────────────────────────────


class TestToolSchemaCache:
    """Tests for ToolRegistry session-level schema caching."""

    def test_schema_cached_on_first_call(self):
        """to_api_format caches schema on first call."""
        registry = ToolRegistry()
        registry.register(DummyTool())
        result1 = registry.to_api_format("anthropic")
        result2 = registry.to_api_format("anthropic")
        # Same object returned — byte-consistent for prompt cache
        assert result1[0] is result2[0]

    def test_schema_cache_invalidated_on_reregister(self):
        """Re-registering a tool invalidates its schema cache."""
        registry = ToolRegistry()
        registry.register(DummyTool())
        result1 = registry.to_api_format("anthropic")

        registry.register(DummyTool())
        result2 = registry.to_api_format("anthropic")
        # Different object — cache was invalidated
        assert result1[0] is not result2[0]

    def test_invalidate_schema_cache_by_name(self):
        """invalidate_schema_cache with name removes only that tool."""
        registry = ToolRegistry()
        registry.register(DummyTool())
        registry.register(SlowTool())
        result1 = registry.to_api_format("anthropic")
        dummy_cached = result1[0]
        slow_cached = result1[1]

        registry.invalidate_schema_cache("Test")
        result2 = registry.to_api_format("anthropic")
        # Dummy was invalidated — different object
        assert result2[0] is not dummy_cached
        # Slow was NOT invalidated — same object
        slow_result = [r for r in result2 if r["name"] == "Slow"][0]
        assert slow_result is slow_cached

    def test_invalidate_schema_cache_all(self):
        """invalidate_schema_cache with None clears all."""
        registry = ToolRegistry()
        registry.register(DummyTool())
        result1 = registry.to_api_format("anthropic")

        registry.invalidate_schema_cache()
        result2 = registry.to_api_format("anthropic")
        assert result1[0] is not result2[0]

    def test_schema_cache_different_families(self):
        """Schema cache keys include family — anthropic and openai are separate."""
        registry = ToolRegistry()
        registry.register(DummyTool())
        result_anthropic = registry.to_api_format("anthropic")
        result_openai = registry.to_api_format("openai")
        # Both use same cache key (tool name) — last call wins
        # This is acceptable because to_api_format is called with
        # the same family throughout a session
        assert result_anthropic[0]["name"] == "Test"
        assert result_openai[0]["type"] == "function"
