"""Built-in slash commands — mirrors commands/ directory."""

from __future__ import annotations

from typing import Callable


class Command:
    """A slash command definition."""

    def __init__(self, name: str, description: str, handler: Callable) -> None:
        self.name = name
        self.description = description
        self.handler = handler


BUILTIN_COMMANDS: dict[str, Command] = {}


def command(name: str, description: str):
    """Decorator to register a built-in command."""
    def decorator(func: Callable) -> Callable:
        BUILTIN_COMMANDS[name] = Command(name=name, description=description, handler=func)
        return func
    return decorator
