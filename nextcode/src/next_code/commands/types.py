"""Command type system — LocalCommand, PromptCommand, CommandResult, CommandContext."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable


class CommandType(Enum):
    """Classification of how a command is executed."""
    LOCAL = "local"
    PROMPT = "prompt"


class ResultType(Enum):
    """What kind of result a command produced.

    Callers (REPL, Bridge) interpret each type differently for their output mechanism.
    """
    TEXT = "text"
    CLEAR = "clear"
    SHUTDOWN = "shutdown"
    CONTEXT_INFO = "context_info"
    COMPACT_COMPLETE = "compact_complete"
    UNKNOWN_COMMAND = "unknown_command"
    PROMPT = "prompt"


@dataclass
class CommandResult:
    """Result returned by every command handler.

    The `type` field tells the caller what happened.
    The `content` field carries type-specific data.
    """
    type: ResultType
    content: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandContext:
    """Read-only context provided to command handlers.

    Supplies everything commands need without giving them
    write access to REPL/Bridge internals.
    """
    model: str | None
    store_get: Callable[[str, Any], Any]
    store_set: Callable[[str, Any], None]
    loop_state: Any
    clear_system_context_cache: Callable[[], None]
    registry: Any  # CommandRegistry — use Any to avoid circular import


# Type alias for command handler functions
CommandHandler = Callable[[str, CommandContext], Awaitable[CommandResult]]

# Type alias for prompt generator functions (used by PromptCommand / Skills)
PromptGenerator = Callable[[str, CommandContext], Awaitable[list[dict[str, Any]]]]


@dataclass
class CommandBase:
    """Base definition for a registered command."""
    name: str
    description: str
    command_type: CommandType
    handler: CommandHandler
    aliases: list[str] = field(default_factory=list)


@dataclass
class LocalCommand(CommandBase):
    """A command that executes locally and returns a CommandResult."""
    command_type: CommandType = field(default=CommandType.LOCAL, init=False)


@dataclass
class PromptCommand(CommandBase):
    """A command that generates a prompt for the model.

    PromptCommands are the basis of the Skill system: a Markdown file
    with YAML frontmatter is converted into a PromptCommand whose
    ``get_prompt`` callable produces the prompt content sent to the model.

    The ``handler`` field is still required by CommandBase but is unused
    for PromptCommands — dispatch goes through ``get_prompt`` instead.
    """
    command_type: CommandType = field(default=CommandType.PROMPT, init=False)
    source: str = "builtin"        # 'builtin' | 'skills' | 'mcp' | 'plugin' | 'bundled'
    loaded_from: str | None = None # 'skills' | 'bundled' | 'mcp' | None
    progress_message: str = "running"
    arg_names: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    model: str | None = None
    context: str = "inline"        # 'inline' | 'fork'
    agent: str | None = None
    effort: str | None = None
    when_to_use: str | None = None
    user_invocable: bool = True
    is_hidden: bool = False
    paths: list[str] = field(default_factory=list)
    get_prompt: PromptGenerator | None = None


# Union type for any command definition
Command = LocalCommand | PromptCommand
