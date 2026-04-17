"""System context - appended to system prompt.

Dynamic context that changes per session (Git status, etc.).
Cached during prefetch for improved performance.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any


def _find_git_root(start: str) -> str | None:
    """Find git root directory by searching upward."""
    current = Path(start).resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return str(current)
        current = current.parent
    return None


def _get_git_info(cwd: str) -> dict[str, Any]:
    """Get Git repository information."""
    try:
        git_root = _find_git_root(cwd)

        if git_root is None:
            return {
                "branch": "",
                "status": "",
                "recent_commits": "",
                "working_directory": cwd or "",
            }

        # Get current branch
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=git_root,
            capture_output=True,
            text=True,
            timeout=5,
        )

        current_branch = ""
        if result.returncode == 0:
            current_branch = result.stdout.strip()

        # Get modified files count
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=git_root,
            capture_output=True,
            text=True,
            timeout=5,
        )

        modified_count = 0
        if result.returncode == 0:
            modified_count = len([line for line in result.stdout.strip().split('\n') if line.strip()])

        return {
            "branch": current_branch,
            "status": f"{modified_count} modified file(s)" if modified_count > 0 else "clean",
            "recent_commits": "",
            "working_directory": cwd or "",
            "git_root": git_root,
        }

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # Git not available or not in git repo
        return {
            "branch": "",
            "status": "",
            "recent_commits": "",
            "working_directory": cwd or "",
        }


@lru_cache(maxsize=1)
def get_system_context(cwd: str | None) -> dict[str, Any]:
    """Get system context (Git status, etc.).

    This is cached for the session - context is computed during prefetch.

    Args:
        cwd: Current working directory

    Returns:
        Dictionary with Git information
    """
    if cwd:
        # Git status (computed on demand)
        git_info = _get_git_info(cwd)
        return {
            "branch": git_info.get("branch", ""),
            "status": git_info.get("status", ""),
            "recent_commits": "",
        }

    return {"branch": "", "status": "", "recent_commits": ""}


def format_system_context(context: dict[str, Any]) -> str:
    """Format system context for inclusion in prompt.

    Args:
        context: System context dictionary

    Returns:
        Formatted string for inclusion in system prompt
    """
    if not context or not context.get("branch"):
        return ""

    lines = ["## System Context"]

    # Add branch info
    if context.get("branch"):
        lines.append(f"Branch: {context['branch']}")

    # Add status info
    if context.get("status"):
        lines.append(f"Status: {context['status']}")

    return "\n".join(lines)


def clear_system_context_cache() -> None:
    """Clear system context cache."""
    get_system_context.cache_clear()
