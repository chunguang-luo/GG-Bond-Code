"""User context - prepended to user messages."""

from __future__ import annotations

from functools import lru_cache
from datetime import datetime
from pathlib import Path
from typing import Any

from next_code.context.nextcodemd import load_all_nextcode_md
from next_code.memory.index import read_index as read_memory_index


def _find_project_root(start: str) -> str:
    """Find project root by looking for .git or .nextcode directory."""
    current = Path(start).resolve()
    while current != current.parent:
        if (current / ".git").exists() or (current / ".nextcode").exists():
            return str(current)
        current = current.parent
    return str(Path(start).resolve())


@lru_cache(maxsize=1)
def get_user_context(cwd: str | None = None) -> dict[str, str]:
    """Get user context (NEXTCODE.md, date, etc.).

    This is memoized for the entire session.
    """
    context = {
        "current_date": f"Today's date is {datetime.now().strftime('%Y/%m/%d')}.",
    }

    if cwd:
        nextcode_md = load_all_nextcode_md(cwd)
        if nextcode_md:
            context["nextcode_md"] = nextcode_md

        # Memory system index
        memory_index = read_memory_index(cwd)
        if memory_index and memory_index.strip():
            context["memory_index"] = memory_index.strip()

    return context


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
    if "nextcode_md" in context:
        context_parts.append(f"# NEXTCODE Project Memory\n{context['nextcode_md']}")
    if "memory_index" in context:
        context_parts.append(f"# Memory Index\n{context['memory_index']}")
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
