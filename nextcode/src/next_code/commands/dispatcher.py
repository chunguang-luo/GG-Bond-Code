"""CommandDispatcher — dispatch slash commands through the registry."""

from __future__ import annotations

from .types import (
    CommandContext,
    CommandResult,
    CommandType,
    ResultType,
)
from .registry import CommandRegistry


class CommandDispatcher:
    """Dispatch slash commands through the registry.

    Responsibilities:
    - Parse raw input into command name + args
    - Look up command in registry
    - Execute the handler with a CommandContext
    - Handle unknown commands with similar-command suggestions
    - Reject PromptCommand invocations in Phase 1
    """

    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry

    async def dispatch(
        self,
        raw_input: str,
        context: CommandContext,
    ) -> CommandResult:
        """Dispatch a slash command and return its result.

        Args:
            raw_input: The full user input including the leading slash
                       (e.g. "/compact", "/model", "/exit quit")
            context: Read-only context for command execution

        Returns:
            CommandResult that the caller interprets for its output mechanism.
        """
        stripped = raw_input.strip()
        if not stripped.startswith("/"):
            return CommandResult(
                type=ResultType.TEXT,
                content={"message": "Not a command."},
            )

        # Split into command name and remaining args
        parts = stripped.split(maxsplit=1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # Look up command
        command = self._registry.lookup(cmd_name)
        if command is None:
            suggestion = self._find_similar_command(cmd_name)
            return CommandResult(
                type=ResultType.UNKNOWN_COMMAND,
                content={
                    "command": cmd_name,
                    "suggestion": suggestion,
                },
            )

        # Phase 1: reject PromptCommand
        if command.command_type == CommandType.PROMPT:
            return CommandResult(
                type=ResultType.TEXT,
                content={
                    "message": f"Command '{cmd_name}' is not yet implemented (prompt-based).",
                },
            )

        # Execute handler
        return await command.handler(args, context)

    def _find_similar_command(self, cmd: str) -> str | None:
        """Find similar command suggestion using prefix matching."""
        valid_commands = self._registry.all_names()
        best_match = None
        best_score = 0

        for valid_cmd in valid_commands:
            score = 0
            min_len = min(len(cmd), len(valid_cmd))
            for i in range(min_len):
                if cmd[i] == valid_cmd[i]:
                    score += 1
                else:
                    break
            if len(cmd) == len(valid_cmd):
                score += 1
            if score > best_score and score >= 2:
                best_score = score
                best_match = valid_cmd

        return best_match
