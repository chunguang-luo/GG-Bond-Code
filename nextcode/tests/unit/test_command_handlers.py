"""Unit tests for individual command handlers."""

import asyncio

from next_code.commands.registry import CommandRegistry
from next_code.commands.types import CommandResult, CommandContext, ResultType
from next_code.commands.clear import handle_clear
from next_code.commands.compact import handle_compact
from next_code.commands.help import handle_help
from next_code.commands.thinking import handle_thinking
from next_code.commands.model import handle_model
from next_code.commands.exit import handle_exit


def _make_context(**overrides):
    store: dict[str, object] = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]}

    defaults = {
        "model": "deepseek-chat",
        "store_get": lambda k, d=None: store.get(k, d),
        "store_set": lambda k, v: store.__setitem__(k, v),
        "loop_state": None,
        "clear_system_context_cache": lambda: None,
        "registry": CommandRegistry(),
    }
    defaults.update(overrides)
    ctx = CommandContext(**defaults)
    return ctx, store


class TestClearHandler:
    def test_clear(self):
        ctx, store = _make_context()
        result = asyncio.run(handle_clear("", ctx))
        assert result.type == ResultType.CLEAR
        assert store["messages"] == []


class TestCompactHandler:
    def test_compact_no_messages(self):
        ctx, store = _make_context()
        store["messages"] = []
        result = asyncio.run(handle_compact("", ctx))
        assert result.type == ResultType.TEXT
        assert "No messages" in result.content["message"]


class TestHelpHandler:
    def test_help_lists_commands(self):
        registry = CommandRegistry()
        from next_code.commands.exit import create as create_exit
        from next_code.commands.model import create as create_model
        registry.register(create_exit())
        registry.register(create_model())

        ctx, _ = _make_context(registry=registry)
        result = asyncio.run(handle_help("", ctx))
        assert result.type == ResultType.TEXT
        assert "/exit" in result.content["message"]
        assert "/model" in result.content["message"]


class TestThinkingHandler:
    def test_toggle_on(self):
        ctx, store = _make_context()
        store["ui.show_thinking"] = False
        result = asyncio.run(handle_thinking("", ctx))
        assert result.type == ResultType.TEXT
        assert "ON" in result.content["message"]
        assert store["ui.show_thinking"] is True

    def test_toggle_off(self):
        ctx, store = _make_context()
        store["ui.show_thinking"] = True
        result = asyncio.run(handle_thinking("", ctx))
        assert result.type == ResultType.TEXT
        assert "OFF" in result.content["message"]
        assert store["ui.show_thinking"] is False


class TestModelHandler:
    def test_show_model(self):
        ctx, _ = _make_context()
        result = asyncio.run(handle_model("", ctx))
        assert result.type == ResultType.TEXT
        assert "deepseek-chat" in result.content["message"]

    def test_unknown_model(self):
        ctx, store = _make_context()
        store["model"] = "gpt-4"
        result = asyncio.run(handle_model("", ctx))
        assert "gpt-4" in result.content["message"]


class TestExitHandler:
    def test_exit(self):
        ctx, _ = _make_context()
        result = asyncio.run(handle_exit("", ctx))
        assert result.type == ResultType.SHUTDOWN
        assert result.content["reason"] == "user exit"
