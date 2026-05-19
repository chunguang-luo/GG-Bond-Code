"""Conditional skill activation — mirrors activateConditionalSkillsForPaths.

Skills can declare ``paths`` globs in their frontmatter.  A skill with
non-empty ``paths`` starts in a *pending* state — it is registered in the
CommandRegistry but its ``when_to_use`` is not surfaced to the model until
the user touches a matching file.

When a file operation (Read, Edit, Write, Glob, Grep, Bash) touches a
file that matches a pending skill's paths pattern, the skill transitions
to *activated* and its ``when_to_use`` hint becomes visible to the model
through the SkillTool description.
"""

from __future__ import annotations

import logging
from fnmatch import fnmatch
from pathlib import Path

from ..commands.types import PromptCommand

logger = logging.getLogger(__name__)


class ConditionalSkillManager:
    """Manage conditional skill activation state.

    Tracks two sets of skills:
    - **Pending**: skills with ``paths`` that haven't matched any file yet.
    - **Activated**: skills whose ``paths`` have matched at least one file.

    The manager is typically owned by the IPCBridge and consulted after
    each tool execution that involves file paths.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PromptCommand] = {}   # name → command
        self._activated: dict[str, PromptCommand] = {}  # name → command

    def register_conditional(self, command: PromptCommand) -> None:
        """Register a conditional skill (paths non-empty).

        Only skills with non-empty ``paths`` are tracked; others are
        immediately considered activated.
        """
        if not command.paths:
            # No paths condition → immediately activated
            self._activated[command.name] = command
            return
        self._pending[command.name] = command

    def activate_for_paths(self, file_paths: list[str], cwd: str) -> list[str]:
        """Check file paths against pending skills and activate matches.

        Args:
            file_paths: Absolute or relative file paths from recent tool use.
            cwd: Current working directory, used to compute relative paths
                 for pattern matching.

        Returns:
            List of skill names that were newly activated.
        """
        activated_names: list[str] = []

        for name, cmd in list(self._pending.items()):
            matched = False
            for file_path in file_paths:
                rel = self._make_relative(file_path, cwd)
                for pattern in cmd.paths:
                    if fnmatch(rel, pattern) or fnmatch(file_path, pattern):
                        self._activated[name] = cmd
                        del self._pending[name]
                        activated_names.append(name)
                        matched = True
                        break
                if matched:
                    break

        if activated_names:
            logger.info("Activated conditional skills: %s", activated_names)

        return activated_names

    def get_activated(self) -> list[PromptCommand]:
        """Return all activated conditional skills."""
        return list(self._activated.values())

    def get_pending(self) -> list[PromptCommand]:
        """Return all pending (not yet activated) conditional skills."""
        return list(self._pending.values())

    def get_all_conditional(self) -> list[PromptCommand]:
        """Return all conditional skills (pending + activated)."""
        return list(self._pending.values()) + list(self._activated.values())

    def is_activated(self, name: str) -> bool:
        """Check whether a conditional skill has been activated."""
        return name in self._activated

    def clear(self) -> None:
        """Reset all activation state."""
        self._pending.clear()
        self._activated.clear()

    @staticmethod
    def _make_relative(file_path: str, cwd: str) -> str:
        """Compute a relative path for pattern matching.

        Falls back to the original path if it's not under cwd.
        """
        try:
            return str(Path(file_path).relative_to(cwd))
        except ValueError:
            return file_path
