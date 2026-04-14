"""Multi-layer settings — unified .settings.json config."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "api_key": "",
    "model": "deepseek-chat",
    "permissions": {
        "allow": [],
        "deny": [],
    },
    "context": {
        "max_tokens": 65536,
        "compact_threshold": 0.8,
    },
}

_settings: dict[str, Any] = {}


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file, return empty dict on failure."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    """Save data to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_settings() -> None:
    """Load settings from global + project .settings.json files."""
    global _settings

    # Start with defaults
    _settings = dict(_DEFAULTS)

    # Layer 1: Global config (~/.ggbond/.settings.json)
    global_path = Path.home() / ".ggbond" / ".settings.json"
    _deep_merge(_settings, _load_json(global_path))

    # Layer 2: Project config (.ggbond/.settings.json)
    project_path = Path.cwd() / ".ggbond" / ".settings.json"
    _deep_merge(_settings, _load_json(project_path))

    # Layer 3: Environment variables override
    if env_key := os.environ.get("GGBOND_API_KEY"):
        _settings["api_key"] = env_key
    if env_model := os.environ.get("GGBOND_MODEL"):
        _settings["model"] = env_model


def get_setting(key: str, default: Any = None) -> Any:
    """Get a setting value by dot-separated key."""
    parts = key.split(".")
    value = _settings
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return default
    return value


def get_all_settings() -> dict[str, Any]:
    """Return a snapshot of all settings."""
    return dict(_settings)


def show_config() -> None:
    """Print current configuration."""
    print(json.dumps(_settings, indent=2, ensure_ascii=False))


def _deep_merge(base: dict, override: dict) -> None:
    """Merge override into base, recursively for nested dicts."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
