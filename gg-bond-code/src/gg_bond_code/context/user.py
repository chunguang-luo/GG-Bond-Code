"""User context - prepended to user messages."""

from __future__ import annotations

from functools import lru_cache
from datetime import datetime
from pathlib import Path
from typing import Any


def _find_project_root(start: str) -> str:
    """Find project root by looking for .git or .ggbond directory."""
    current = Path(start).resolve()
    while current != current.parent:
        if (current / ".git").exists() or (current / ".ggbond").exists():
            return str(current)
        current = current.parent
    return str(Path(start).resolve())


@lru_cache(maxsize=1)
def get_user_context(cwd: str | None = None) -> dict[str, str]:
    """Get user context (GGBOND.md, date, etc.).

    This is memoized for the entire session.
    """
    context = {
        "current_date": f"Today's date is {datetime.now().strftime('%Y/%m/%d')}.",
    }

    if cwd:
        ggbond_md = _load_ggbond_md(cwd)
        if ggbond_md:
            context["ggbond_md"] = ggbond_md

    return context


def _load_ggbond_md(cwd: str) -> str | None:
    """Load GGBOND.md from .ggbond directory in project root."""
    try:
        project_root = _find_project_root(cwd)
        ggbond_dir = Path(project_root) / ".ggbond"
        ggbond_path = ggbond_dir / "GGBOND.md"

        if not ggbond_path.exists():
            return None

        with open(ggbond_path, "r", encoding="utf-8") as f:
            content = f.read()

        if not content.strip():
            return None

        return content.strip()

    except (IOError, OSError):
        return None


def prepend_user_context(
    messages: list[dict[str, Any]],
    context: dict[str, str],
) -> list[dict[str, Any]]:
    """Prepend user context to the first user message."""
    if not messages or not context:
        return messages

    # Only prepend if first message is from user
    if messages[0].get("role") != "user":
        return messages

    # Build context block
    context_parts = []
    if "ggbond_md" in context:
        context_parts.append(f"# GGBOND Project Memory\n{context['ggbond_md']}")
    if "current_date" in context:
        context_parts.append(context["current_date"])

    if not context_parts:
        return messages

    # Prepend context to first user message
    context_block = "\n\n".join(context_parts)
    original_content = messages[0].get("content", "")

    new_messages = messages.copy()
    new_messages[0] = {
        "role": "user",
        "content": f"{context_block}\n\n{original_content}",
    }

    return new_messages


def clear_user_context_cache() -> None:
    """Clear user context cache."""
    get_user_context.cache_clear()
