"""CommandRegistry — register and look up slash commands."""

from __future__ import annotations

from typing import Iterator

from .types import Command


class CommandRegistry:
    """Register and look up slash commands.

    Supports primary name registration, alias registration,
    lookup by name or alias, and iteration over unique commands.
    """

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._primary_names: set[str] = set()

    def register(self, command: Command) -> None:
        """Register a command with its primary name and aliases."""
        if command.name in self._commands:
            raise ValueError(f"Command already registered: {command.name}")

        self._commands[command.name] = command
        self._primary_names.add(command.name)

        for alias in command.aliases:
            if alias in self._commands:
                raise ValueError(
                    f"Alias '{alias}' conflicts with existing command: {alias}"
                )
            self._commands[alias] = command

    def lookup(self, name: str) -> Command | None:
        """Look up a command by name or alias. Returns None if not found."""
        return self._commands.get(name)

    def has(self, name: str) -> bool:
        """Check if a command name or alias exists."""
        return name in self._commands

    def all_commands(self) -> Iterator[Command]:
        """Iterate over unique commands (primary names only)."""
        for name in self._primary_names:
            yield self._commands[name]

    def all_names(self) -> list[str]:
        """Return all registered names and aliases, sorted."""
        return sorted(self._commands.keys())

    def primary_names(self) -> list[str]:
        """Return primary command names only, sorted."""
        return sorted(self._primary_names)
