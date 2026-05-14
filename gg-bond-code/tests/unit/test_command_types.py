"""Unit tests for command type system."""

from gg_bond_code.commands.types import (
    CommandBase,
    CommandContext,
    CommandType,
    LocalCommand,
    PromptCommand,
    CommandResult,
    ResultType,
)


class TestResultType:
    def test_values(self):
        assert ResultType.TEXT.value == "text"
        assert ResultType.CLEAR.value == "clear"
        assert ResultType.SHUTDOWN.value == "shutdown"
        assert ResultType.CONTEXT_INFO.value == "context_info"
        assert ResultType.COMPACT_COMPLETE.value == "compact_complete"
        assert ResultType.UNKNOWN_COMMAND.value == "unknown_command"


class TestCommandResult:
    def test_text_result(self):
        result = CommandResult(type=ResultType.TEXT, content={"message": "hello"})
        assert result.type == ResultType.TEXT
        assert result.content["message"] == "hello"

    def test_clear_result_default_content(self):
        result = CommandResult(type=ResultType.CLEAR)
        assert result.type == ResultType.CLEAR
        assert result.content == {}

    def test_shutdown_result(self):
        result = CommandResult(type=ResultType.SHUTDOWN, content={"reason": "user exit"})
        assert result.content["reason"] == "user exit"


class TestLocalCommand:
    def test_command_type_is_local(self):
        async def handler(args, ctx):
            return CommandResult(type=ResultType.TEXT, content={"message": "ok"})

        cmd = LocalCommand(name="/test", description="Test", handler=handler)
        assert cmd.command_type == CommandType.LOCAL

    def test_default_aliases(self):
        async def handler(args, ctx):
            return CommandResult(type=ResultType.TEXT)

        cmd = LocalCommand(name="/test", description="Test", handler=handler)
        assert cmd.aliases == []


class TestPromptCommand:
    def test_command_type_is_prompt(self):
        async def handler(args, ctx):
            return CommandResult(type=ResultType.TEXT)

        cmd = PromptCommand(name="/skill", description="Skill", handler=handler)
        assert cmd.command_type == CommandType.PROMPT


class TestCommandContext:
    def test_required_fields(self):
        ctx = CommandContext(
            model="test-model",
            store_get=lambda k, d=None: d,
            store_set=lambda k, v: None,
            loop_state=None,
            clear_system_context_cache=lambda: None,
            registry=None,
        )
        assert ctx.model == "test-model"
