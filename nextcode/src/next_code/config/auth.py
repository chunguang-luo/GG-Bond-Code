"""Authentication — API key resolution and configuration."""

from __future__ import annotations

import os

from .settings import get_setting, update_setting

from pathlib import Path


def resolve_api_key() -> str | None:
    """Resolve API key based on current model.

    - claude-* → ANTHROPIC_API_KEY env → settings api_key
    - deepseek-* / glm-* → NEXTCODE_API_KEY env → settings api_key
    - unknown  → try both env vars, but warn about model name
    """
    import logging
    from ..api.client import _model_family

    logger = logging.getLogger(__name__)

    model = get_setting("model", "")
    family = _model_family(model)

    if family == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
    elif family == "openai":
        key = os.environ.get("NEXTCODE_API_KEY")
    else:
        # Unknown model: try both env vars
        key = os.environ.get("NEXTCODE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if model:
            logger.warning(
                "Unknown model '%s'. "
                "Please configure a valid model name via --model, NEXTCODE_MODEL, "
                "or ~/.nextcode/.settings.json.",
                model,
            )

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


def configure_interactive() -> None:
    """Interactive guided setup for API key, base URL, and model.

    Prompts for any missing required configuration and saves to
    the global ~/.nextcode/.settings.json file.
    """
    from .settings import _load_json, _save_json

    config_path = Path.home() / ".nextcode" / ".settings.json"
    data = _load_json(config_path)

    current_key = get_setting("api_key", "")
    current_url = get_setting("base_url", "")
    current_model = get_setting("model", "")

    # API Key
    if current_key:
        print(f"  API key: {'*' * 8}{current_key[-4:]} (already set)")
    else:
        key = input("  API key: ").strip()
        if key:
            data["api_key"] = key
            update_setting("api_key", key)

    # Base URL
    if current_url:
        print(f"  Base URL: {current_url} (already set)")
    else:
        print("  Base URL (e.g. https://api.deepseek.com, https://api.openai.com/v1):")
        url = input("  > ").strip()
        if url:
            data["base_url"] = url
            update_setting("base_url", url)

    # Model
    if current_model:
        print(f"  Model: {current_model} (already set)")
    else:
        print("  Model name (e.g. deepseek-chat, claude-sonnet-4-5-20250514, gpt-4o):")
        model = input("  > ").strip()
        if model:
            data["model"] = model
            update_setting("model", model)

    # Save to global config
    _save_json(config_path, data)
    print(f"\n  Configuration saved to {config_path}\n")
