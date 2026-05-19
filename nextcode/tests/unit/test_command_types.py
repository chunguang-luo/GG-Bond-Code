"""Unit tests for command type system."""

from next_code.commands.types import (
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
        assert ResultType.PROMPT.value == "prompt"


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

    def test_skill_fields_defaults(self):
        async def handler(args, ctx):
            return CommandResult(type=ResultType.TEXT)

        cmd = PromptCommand(name="/skill", description="Skill", handler=handler)
        assert cmd.source == "builtin"
        assert cmd.loaded_from is None
        assert cmd.progress_message == "running"
        assert cmd.arg_names == []
        assert cmd.allowed_tools == []
        assert cmd.model is None
        assert cmd.context == "inline"
        assert cmd.agent is None
        assert cmd.effort is None
        assert cmd.when_to_use is None
        assert cmd.user_invocable is True
        assert cmd.is_hidden is False
        assert cmd.paths == []
        assert cmd.get_prompt is None

    def test_skill_fields_custom(self):
        async def handler(args, ctx):
            return CommandResult(type=ResultType.TEXT)

        async def get_prompt(args, ctx):
            return [{"type": "text", "text": "hello"}]

        cmd = PromptCommand(
            name="/review",
            description="Review code",
            handler=handler,
            source="skills",
            loaded_from="skills",
            model="opus",
            context="fork",
            allowed_tools=["Bash"],
            get_prompt=get_prompt,
        )
        assert cmd.source == "skills"
        assert cmd.loaded_from == "skills"
        assert cmd.model == "opus"
        assert cmd.context == "fork"
        assert cmd.allowed_tools == ["Bash"]
        assert cmd.get_prompt is not None


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
