"""Built-in slash commands — registry factory and public exports."""

from __future__ import annotations

from .types import (
    Command,
    LocalCommand,
    PromptCommand,
    CommandResult,
    CommandContext,
    CommandType,
    ResultType,
)
from .registry import CommandRegistry
from .dispatcher import CommandDispatcher


def create_builtin_registry() -> CommandRegistry:
    """Create and populate the registry with all built-in commands."""
    from .clear import create as create_clear
    from .compact import create as create_compact
    from .help import create as create_help
    from .context import create as create_context
    from .thinking import create as create_thinking
    from .model import create as create_model
    from .log import create as create_log
    from .exit import create as create_exit

    registry = CommandRegistry()

    for factory in [
        create_clear,
        create_compact,
        create_help,
        create_context,
        create_thinking,
        create_model,
        create_log,
        create_exit,
    ]:
        registry.register(factory())

    return registry


__all__ = [
    "Command",
    "LocalCommand",
    "PromptCommand",
    "CommandResult",
    "CommandContext",
    "CommandType",
    "ResultType",
    "CommandRegistry",
    "CommandDispatcher",
    "create_builtin_registry",
]
