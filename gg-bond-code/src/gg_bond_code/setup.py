"""Session-level setup — mirrors setup.ts."""

from __future__ import annotations

import os
from pathlib import Path

from gg_bond_code.state.store import Store


def setup(cwd: str, model: str | None = None) -> None:
    """Initialize session state. Only called for interactive/print sessions."""
    # 1. Set working directory
    os.chdir(cwd)

    # 2. Initialize global store
    store = Store()
    store.set("cwd", cwd)


    # Model priority: CLI flag → config.toml → default
    if not model:
        from gg_bond_code.config.settings import get_setting
        model = get_setting("model", "deepseek-chat")
    store.set("model", model)

    # 3. Discover project root (walk up to find .git or .ggbond)
    project_root = _find_project_root(cwd)
    store.set("project_root", project_root)

    # 4. Initialize conversation history
    store.set("messages", [])


def _find_project_root(start: str) -> str:
    """Walk up from start to find project root (.git or .ggbond marker)."""
    current = Path(start).resolve()
    while current != current.parent:
        if (current / ".git").exists() or (current / ".ggbond").exists():
            return str(current)
        current = current.parent
    return str(Path(start).resolve())
