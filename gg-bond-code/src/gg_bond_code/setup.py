"""Session-level setup — mirrors setup.ts."""

from __future__ import annotations

import os
from pathlib import Path

from .state.store import Store, reset_store
from .config.settings import get_setting, is_persistable_key, update_setting


def _on_store_change(key: str, new_value: object, old_value: object) -> None:
    """Store onChange callback — syncs persistable keys back to Settings.

    Mirrors Claude Code's onChangeAppState: a single place where state
    changes trigger side-effects (persistence, external notifications).
    """
    if is_persistable_key(key):
        update_setting(key, new_value)


def setup(cwd: str, model: str | None = None) -> None:
    """Initialize session state. Only called for interactive/print sessions."""
    # 1. Set working directory
    os.chdir(cwd)

    # 2. Initialize global store with onChange for Store ↔ Settings sync
    reset_store(on_change=_on_store_change)
    store = Store()
    store.set("cwd", cwd)

    # Model priority: CLI flag → config → default
    if not model:
        model = get_setting("model", "deepseek-chat")
    store.set("model", model)

    # 3. Discover project root (walk up to find .git or .ggbond)
    project_root = _find_project_root(cwd)
    store.set("project_root", project_root)

    # 4. Initialize conversation history
    store.set("messages", [])

    # 5. Initialize UI preferences in Store (optimization 3)
    store.set("ui.show_thinking", False)
    store.set("ui.show_tool_details", False)


def _find_project_root(start: str) -> str:
    """Walk up from start to find project root (.git or .ggbond marker)."""
    current = Path(start).resolve()
    while current != current.parent:
        if (current / ".git").exists() or (current / ".ggbond").exists():
            return str(current)
        current = current.parent
    return str(Path(start).resolve())
