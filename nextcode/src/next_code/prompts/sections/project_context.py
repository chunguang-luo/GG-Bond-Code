"""Project context section - dynamic section with cwd, platform, user info."""

from __future__ import annotations

import os
from pathlib import Path


def _find_project_root(start: str) -> str:
    """Find project root by looking for .git or .nextcode directory."""
    current = Path(start).resolve()
    while current != current.parent:
        if (current / ".git").exists() or (current / ".nextcode").exists():
            return str(current)
        current = current.parent
    return str(Path(start).resolve())


def _project_context_content(cwd: str) -> str:
    """Return to project context section content."""
    project_root = _find_project_root(cwd)
    return f"""## Project Context

Working directory: {cwd}
Project root: {project_root}
Platform: {os.name}
Current user: {os.environ.get('USER', 'unknown')}"""


# Create dynamic section object (may change between sessions)
section = lambda cwd=None: _project_context_content(cwd) if cwd else None
