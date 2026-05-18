"""Unit tests for CommandDispatcher."""

import asyncio

from next_code.commands.registry import CommandRegistry
from next_code.commands.dispatcher import CommandDispatcher
from next_code.commands.types import (
    CommandContext,
    CommandResult,
    CommandType,
    LocalCommand,
    PromptCommand,
    ResultType,
)


def _make_context(**overrides):
    store: dict[str, object] = {}

    defaults = {
        "model": "deepseek-chat",
        "store_get": lambda k, d=None: store.get(k, d),
        "store_set": lambda k, v: store.__setitem__(k, v),
        "loop_state": None,
        "clear_system_context_cache": lambda: None,
        "registry": CommandRegistry(),
    }
    defaults.update(overrides)
    return CommandContext(**defaults)


def _make_registry_with_commands():
    registry = CommandRegistry()

    async def handle_model(args, ctx):
        return CommandResult(type=ResultType.TEXT, content={"message": f"Model: {ctx.model}"})

    async def handle_exit(args, ctx):
        return CommandResult(type=ResultType.SHUTDOWN, content={"reason": "user exit"})

    async def handle_clear(args, ctx):
        ctx.store_set("messages", [])
        return CommandResult(type=ResultType.CLEAR)

    registry.register(LocalCommand(name="/model", description="Show model", handler=handle_model))
    registry.register(LocalCommand(name="/exit", description="Exit", handler=handle_exit, aliases=["/quit"]))
    registry.register(LocalCommand(name="/clear", description="Clear", handler=handle_clear))

    return registry


class TestCommandDispatcher:
    def test_dispatch_known_command(self):
        registry = _make_registry_with_commands()
        dispatcher = CommandDispatcher(registry)
        ctx = _make_context(registry=registry)
        result = asyncio.run(
            dispatcher.dispatch("/model", ctx)
        )
        assert result.type == ResultType.TEXT
        assert "deepseek-chat" in result.content["message"]

    def test_dispatch_unknown_command(self):
        registry = _make_registry_with_commands()
        dispatcher = CommandDispatcher(registry)
        ctx = _make_context(registry=registry)
        result = asyncio.run(
            dispatcher.dispatch("/unknown", ctx)
        )
        assert result.type == ResultType.UNKNOWN_COMMAND
        assert result.content["command"] == "/unknown"

    def test_dispatch_with_args(self):
        registry = _make_registry_with_commands()
        dispatcher = CommandDispatcher(registry)

        async def handle_echo(args, ctx):
            return CommandResult(type=ResultType.TEXT, content={"message": args})

        registry.register(LocalCommand(name="/echo", description="Echo", handler=handle_echo))
        ctx = _make_context(registry=registry)
        result = asyncio.run(
            dispatcher.dispatch("/echo hello world", ctx)
        )
        assert result.content["message"] == "hello world"

    def test_dispatch_prompt_command_rejected(self):
        registry = CommandRegistry()

        async def handle_prompt(args, ctx):
            return CommandResult(type=ResultType.TEXT)

        registry.register(PromptCommand(name="/skill", description="Skill", handler=handle_prompt))
        dispatcher = CommandDispatcher(registry)
        ctx = _make_context(registry=registry)
        result = asyncio.run(
            dispatcher.dispatch("/skill", ctx)
        )
        assert result.type == ResultType.TEXT
        assert "not yet implemented" in result.content["message"]

    def test_dispatch_clear_command(self):
        registry = _make_registry_with_commands()
        dispatcher = CommandDispatcher(registry)
        ctx = _make_context(registry=registry)
        result = asyncio.run(
            dispatcher.dispatch("/clear", ctx)
        )
        assert result.type == ResultType.CLEAR

    def test_dispatch_exit_command(self):
        registry = _make_registry_with_commands()
        dispatcher = CommandDispatcher(registry)
        ctx = _make_context(registry=registry)
        result = asyncio.run(
            dispatcher.dispatch("/exit", ctx)
        )
        assert result.type == ResultType.SHUTDOWN

    def test_dispatch_via_alias(self):
        registry = _make_registry_with_commands()
        dispatcher = CommandDispatcher(registry)
        ctx = _make_context(registry=registry)
        result = asyncio.run(
            dispatcher.dispatch("/quit", ctx)
        )
        assert result.type == ResultType.SHUTDOWN

    def test_find_similar_command(self):
        registry = _make_registry_with_commands()
        dispatcher = CommandDispatcher(registry)
        suggestion = dispatcher._find_similar_command("/mod")
        assert suggestion == "/model"

    def test_find_similar_command_no_match(self):
        registry = _make_registry_with_commands()
        dispatcher = CommandDispatcher(registry)
        suggestion = dispatcher._find_similar_command("/zzz")
        assert suggestion is None

    def test_non_slash_input(self):
        registry = _make_registry_with_commands()
        dispatcher = CommandDispatcher(registry)
        ctx = _make_context(registry=registry)
        result = asyncio.run(
            dispatcher.dispatch("hello", ctx)
        )
        assert result.type == ResultType.TEXT
        assert "Not a command" in result.content["message"]
