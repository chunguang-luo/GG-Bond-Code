"""Session-level setup — mirrors setup.ts."""

from __future__ import annotations

import os
import time
from pathlib import Path

from .state.store import Store, reset_store
from .config.settings import get_setting, is_persistable_key, update_setting, load_settings


def _on_store_change(key: str, new_value: object, old_value: object) -> None:
    """Store onChange callback — syncs persistable keys back to Settings.

    Mirrors Claude Code's onChangeAppState: a single place where state
    changes trigger side-effects (persistence, external notifications).
    """
    if is_persistable_key(key) and new_value != old_value:
        update_setting(key, new_value)


def setup(cwd: str, model: str | None = None, *, resume_from: str | None = None, title: str | None = None) -> None:
    """Initialize session state. Only called for interactive/print sessions."""
    # 1. Set working directory
    os.chdir(cwd)

    # 2. Initialize global store with onChange for Store ↔ Settings sync
    reset_store(on_change=_on_store_change)
    store = Store()
    store.set("cwd", cwd)

    # Model priority: CLI flag → config → default
    if not model:
        model = get_setting("model", "")
    store.set("model", model)

    # 3. Discover project root (walk up to find .git or .nextcode)
    project_root = _find_project_root(cwd)
    store.set("project_root", project_root)

    # 4. Reload settings now that project_root is set (ensures project config is loaded)
    load_settings()

    # 5. Generate session ID
    from .session import generate_session_id
    session_id = generate_session_id()
    store.set("session_id", session_id)
    store.set("title", title or "")
    store.set("resumed", False)

    # 6. Load session if resuming
    if resume_from:
        from .session import load_session
        data = load_session(resume_from)
        if data:
            store.set("messages", data.get("messages", []))
            store.set("session_id", data.get("session_id", resume_from))
            store.set("title", data.get("title", ""))
            if data.get("model"):
                store.set("model", data["model"])
            store.set("resumed", True)
            store.set("session_start", data.get("started_at", time.time()))
        else:
            import sys
            print(f"Warning: session '{resume_from}' not found. Starting a new session.\n"
                  f"Use `nextcode --sessions` to list saved sessions.", file=sys.stderr)
            store.set("messages", [])
            store.set("session_start", time.time())
    else:
        store.set("messages", [])
        store.set("session_start", time.time())

    # 7. Initialize UI preferences in Store
    store.set("ui.show_thinking", False)
    store.set("ui.show_tool_details", False)


def _find_project_root(start: str) -> str:
    """Walk up from start to find project root (.git or .nextcode marker)."""
    current = Path(start).resolve()
    while current != current.parent:
        if (current / ".git").exists() or (current / ".nextcode").exists():
            return str(current)
        current = current.parent
    return str(Path(start).resolve())
