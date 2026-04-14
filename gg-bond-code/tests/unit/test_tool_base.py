"""Tests for tools/base.py — execute_safe and timeout."""

import asyncio

from gg_bond_code.tools.base import Tool, ToolResult


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
