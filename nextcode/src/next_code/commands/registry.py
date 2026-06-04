"""CommandRegistry — register and look up slash commands."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterator

from .types import Command


class CommandRegistry:
    """Register and look up slash commands.

    Supports primary name registration, alias registration,
    lookup by name or alias, iteration over unique commands,
    and multi-source skill aggregation.
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

    def register_override(self, command: Command) -> None:
        """Register a command, overriding any existing command with the same name.

        Used by skill loading to let user/project skills override builtins.
        """
        # Remove old command + its aliases
        existing = self._commands.get(command.name)
        if existing is not None:
            self._primary_names.discard(command.name)
            aliases_to_remove = [k for k, v in self._commands.items() if v is existing and k != command.name]
            for k in aliases_to_remove:
                del self._commands[k]

        self._commands[command.name] = command
        self._primary_names.add(command.name)

        for alias in command.aliases:
            if alias in self._commands:
                # Remove the alias pointing to a different command
                del self._commands[alias]
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

    async def load_skills(
        self, cwd: str, project_root: str | None = None
    ) -> None:
        """Load skills from user and project skill directories.

        Skills override builtins of the same name (user/project take priority).

        Args:
            cwd: Current working directory — used to locate project-level skills.
            project_root: Project root directory — also searched for skills.
                If None, only cwd is used. This covers the case where the user
                launches from a subdirectory but skills live at the project root.
        """
        from ..skills.loader import load_skills_from_dir

        dirs: list[tuple[Path, str, str]] = [
            (Path.home() / ".nextcode" / "skills", "user", "skills"),
        ]

        # Search cwd
        dirs.append(
            (Path(cwd) / ".nextcode" / "skills", "project", "skills")
        )

        # Also search project_root if distinct from cwd
        if project_root and project_root != cwd:
            dirs.append(
                (Path(project_root) / ".nextcode" / "skills", "project", "skills")
            )

        results = await asyncio.gather(
            *(
                load_skills_from_dir(d, source=src, loaded_from=lf)
                for d, src, lf in dirs
            )
        )

        # Register with override: project skills > user skills > builtins
        for skills in results:
            for cmd in skills:
                self.register_override(cmd)
