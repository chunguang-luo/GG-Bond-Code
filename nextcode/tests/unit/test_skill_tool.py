"""Unit tests for SkillTool — model-to-skill bridge."""

import asyncio
from unittest.mock import MagicMock

from next_code.tools.skill import SkillTool
from next_code.tools.base import ToolResult
from next_code.commands.registry import CommandRegistry
from next_code.commands.types import (
    CommandContext,
    CommandResult,
    LocalCommand,
    PromptCommand,
    ResultType,
)


def _make_context_with_registry(registry: CommandRegistry) -> MagicMock:
    """Create a mock ToolUseContext with a command_registry attached."""
    ctx = MagicMock()
    ctx.command_registry = registry
    ctx.get_state = lambda k: None
    ctx.set_state = lambda k, v: None
    return ctx


def _make_command_registry() -> CommandRegistry:
    registry = CommandRegistry()

    async def handle_review(args: str, ctx: CommandContext) -> CommandResult:
        return CommandResult(type=ResultType.TEXT)

    async def get_review_prompt(args: str, ctx: CommandContext) -> list[dict]:
        return [{"type": "text", "text": f"Review this code: {args}"}]

    # A valid PromptCommand skill
    registry.register(PromptCommand(
        name="/review",
        description="Review code",
        handler=handle_review,
        source="skills",
        get_prompt=get_review_prompt,
        allowed_tools=["Bash", "Read"],
    ))

    # A LocalCommand (not a skill)
    async def handle_clear(args: str, ctx: CommandContext) -> CommandResult:
        return CommandResult(type=ResultType.CLEAR)

    registry.register(LocalCommand(
        name="/clear",
        description="Clear history",
        handler=handle_clear,
    ))

    return registry


class TestSkillTool:
    def test_schema(self):
        tool = SkillTool()
        schema = tool.get_schema()
        assert schema["type"] == "object"
        assert "skill" in schema["properties"]
        assert "args" in schema["properties"]
        assert "skill" in schema["required"]

    def test_is_read_only(self):
        tool = SkillTool()
        assert tool.is_read_only({}) is True

    def test_invoke_skill(self):
        tool = SkillTool()
        registry = _make_command_registry()
        ctx = _make_context_with_registry(registry)

        # Inject context
        from next_code.tools.base import _current_context
        token = _current_context.set(ctx)
        try:
            result = asyncio.run(tool.execute({"skill": "review", "args": "src/main.py"}))
            assert not result.error
            assert "src/main.py" in result.output
        finally:
            _current_context.reset(token)

    def test_invoke_skill_with_slash_prefix(self):
        tool = SkillTool()
        registry = _make_command_registry()
        ctx = _make_context_with_registry(registry)

        from next_code.tools.base import _current_context
        token = _current_context.set(ctx)
        try:
            result = asyncio.run(tool.execute({"skill": "/review", "args": "test.py"}))
            assert not result.error
            assert "test.py" in result.output
        finally:
            _current_context.reset(token)

    def test_unknown_skill(self):
        tool = SkillTool()
        registry = _make_command_registry()
        ctx = _make_context_with_registry(registry)

        from next_code.tools.base import _current_context
        token = _current_context.set(ctx)
        try:
            result = asyncio.run(tool.execute({"skill": "nonexistent"}))
            assert result.error
            assert "Unknown skill" in result.output
        finally:
            _current_context.reset(token)

    def test_local_command_not_skill(self):
        tool = SkillTool()
        registry = _make_command_registry()
        ctx = _make_context_with_registry(registry)

        from next_code.tools.base import _current_context
        token = _current_context.set(ctx)
        try:
            result = asyncio.run(tool.execute({"skill": "clear"}))
            assert result.error
            assert "not a prompt-based skill" in result.output
        finally:
            _current_context.reset(token)

    def test_no_context(self):
        tool = SkillTool()
        from next_code.tools.base import _current_context
        token = _current_context.set(None)
        try:
            result = asyncio.run(tool.execute({"skill": "review"}))
            assert result.error
            assert "no context" in result.output
        finally:
            _current_context.reset(token)

    def test_no_command_registry(self):
        tool = SkillTool()
        ctx = MagicMock()
        ctx.command_registry = None

        from next_code.tools.base import _current_context
        token = _current_context.set(ctx)
        try:
            result = asyncio.run(tool.execute({"skill": "review"}))
            assert result.error
            assert "no command registry" in result.output
        finally:
            _current_context.reset(token)

    def test_skill_no_args(self):
        tool = SkillTool()
        registry = _make_command_registry()
        ctx = _make_context_with_registry(registry)

        from next_code.tools.base import _current_context
        token = _current_context.set(ctx)
        try:
            result = asyncio.run(tool.execute({"skill": "review"}))
            assert not result.error
            assert "Review this code" in result.output
        finally:
            _current_context.reset(token)
