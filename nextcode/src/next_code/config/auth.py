"""Authentication — API key resolution and configuration."""

from __future__ import annotations

import os

from .settings import get_setting, update_setting

from pathlib import Path


def resolve_api_key() -> str | None:
    """Resolve API key based on current model.

    - claude-* / minimax-* → ANTHROPIC_API_KEY env → settings api_key
    - others               → NEXTCODE_API_KEY env → settings api_key
    """
    from ..api.client import _model_family

    model = get_setting("model", "")
    family = _model_family(model)

    if family == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
    else:
        key = os.environ.get("NEXTCODE_API_KEY")

    if not key:
        key = get_setting("api_key", "")

    return key or None


def configure_api_key() -> None:
    """Interactively configure API key, saved to global .settings.json."""
    key = input("Enter your API key: ").strip()
    if not key:
        print("No key provided.")
        return

    # Persist to global config (not project config)
    config_path = Path.home() / ".nextcode" / ".settings.json"
    from .settings import _load_json, _save_json
    data = _load_json(config_path)
    data["api_key"] = key
    _save_json(config_path, data)
    # Also update in-memory settings
    update_setting("api_key", key)
    print(f"API key saved to {config_path}")
