"""Unit tests for CommandRegistry."""

from gg_bond_code.commands.registry import CommandRegistry
from gg_bond_code.commands.types import LocalCommand, CommandResult, ResultType


def _make_command(name: str, description: str = "Test", aliases: list[str] | None = None):
    async def handler(args, ctx):
        return CommandResult(type=ResultType.TEXT, content={"message": f"handled {name}"})

    return LocalCommand(
        name=name,
        description=description,
        handler=handler,
        aliases=aliases or [],
    )


class TestCommandRegistry:
    def test_register_and_lookup(self):
        registry = CommandRegistry()
        cmd = _make_command("/test")
        registry.register(cmd)
        assert registry.lookup("/test") is cmd

    def test_lookup_alias(self):
        registry = CommandRegistry()
        cmd = _make_command("/exit", aliases=["/quit", "/q"])
        registry.register(cmd)
        assert registry.lookup("/quit") is cmd
        assert registry.lookup("/q") is cmd

    def test_lookup_unknown_returns_none(self):
        registry = CommandRegistry()
        assert registry.lookup("/unknown") is None

    def test_has(self):
        registry = CommandRegistry()
        cmd = _make_command("/test", aliases=["/t"])
        registry.register(cmd)
        assert registry.has("/test")
        assert registry.has("/t")
        assert not registry.has("/other")

    def test_register_duplicate_raises(self):
        registry = CommandRegistry()
        registry.register(_make_command("/test"))
        try:
            registry.register(_make_command("/test"))
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_alias_conflict_raises(self):
        registry = CommandRegistry()
        registry.register(_make_command("/test"))
        try:
            registry.register(_make_command("/other", aliases=["/test"]))
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_all_commands_no_duplicates(self):
        registry = CommandRegistry()
        cmd = _make_command("/exit", aliases=["/quit"])
        registry.register(cmd)
        commands = list(registry.all_commands())
        assert len(commands) == 1
        assert commands[0] is cmd

    def test_all_names_includes_aliases(self):
        registry = CommandRegistry()
        registry.register(_make_command("/exit", aliases=["/quit", "/q"]))
        names = registry.all_names()
        assert "/exit" in names
        assert "/quit" in names
        assert "/q" in names

    def test_primary_names(self):
        registry = CommandRegistry()
        registry.register(_make_command("/exit", aliases=["/quit"]))
        registry.register(_make_command("/help"))
        assert registry.primary_names() == ["/exit", "/help"]

    def test_multiple_commands(self):
        registry = CommandRegistry()
        registry.register(_make_command("/help"))
        registry.register(_make_command("/clear"))
        registry.register(_make_command("/exit", aliases=["/quit"]))
        assert len(list(registry.all_commands())) == 3
        assert len(registry.all_names()) == 4  # /help, /clear, /exit, /quit
