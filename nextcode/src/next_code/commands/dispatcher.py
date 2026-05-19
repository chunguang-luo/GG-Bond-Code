"""CommandDispatcher — dispatch slash commands through the registry."""

from __future__ import annotations

from typing import Any

from .types import (
    CommandContext,
    CommandResult,
    CommandType,
    LocalCommand,
    PromptCommand,
    ResultType,
)
from .registry import CommandRegistry


class CommandDispatcher:
    """Dispatch slash commands through the registry.

    Responsibilities:
    - Parse raw input into command name + args
    - Look up command in registry
    - Execute LocalCommand handlers or PromptCommand prompt generators
    - Handle unknown commands with similar-command suggestions
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

        # Dispatch by command type
        if command.command_type == CommandType.LOCAL:
            return await command.handler(args, context)
        elif command.command_type == CommandType.PROMPT:
            return await self._dispatch_prompt(command, args, context)

        return CommandResult(
            type=ResultType.TEXT,
            content={"message": f"Unknown command type: {command.command_type}"},
        )

    async def _dispatch_prompt(
        self,
        command: PromptCommand,
        args: str,
        context: CommandContext,
    ) -> CommandResult:
        """Dispatch a PromptCommand by calling its get_prompt generator."""
        if command.get_prompt is None:
            return CommandResult(
                type=ResultType.TEXT,
                content={"message": f"Skill '{command.name}' has no prompt generator."},
            )

        prompt_blocks = await command.get_prompt(args, context)
        return CommandResult(
            type=ResultType.PROMPT,
            content={
                "prompt_blocks": prompt_blocks,
                "command_name": command.name,
                "source": command.source,
                "allowed_tools": command.allowed_tools,
                "model": command.model,
                "context": command.context,
                "agent": command.agent,
                "effort": command.effort,
            },
        )

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
