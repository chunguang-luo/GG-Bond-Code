"""Multi-layer settings — unified .settings.json config."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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
        "auto_compact_buffer": 13000,
        "blocking_buffer": 3000,
        "circuit_breaker_max_failures": 3,
        "microcompact_keep_recent": 3,
    },
}

_settings: dict[str, Any] = {}

# Keys that should be persisted back to project config when changed via Store
_PERSISTABLE_KEYS: set[str] = {"model"}


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

    # Layer 1: Global config (~/.nextcode/.settings.json)
    global_path = Path.home() / ".nextcode" / ".settings.json"
    if global_path.exists():
        global_data = _load_json(global_path)
        _deep_merge(_settings, global_data)

    # Layer 2: Project config (.nextcode/.settings.json)
    # Use project_root from Store if already loaded, otherwise cwd
    from ..state.store import Store
    project_root = Store().get("project_root", str(Path.cwd()))
    project_path = Path(project_root) / ".nextcode" / ".settings.json"
    if project_path.exists():
        project_data = _load_json(project_path)
        _deep_merge(_settings, project_data)

    # Layer 3: Environment variables override
    # Check both ANTHROPIC_API_KEY and NEXTCODE_API_KEY
    if env_key := os.environ.get("NEXTCODE_API_KEY"):
        _settings["api_key"] = env_key
    elif env_key := os.environ.get("ANTHROPIC_API_KEY"):
        _settings["api_key"] = env_key
    if env_model := os.environ.get("NEXTCODE_MODEL"):
        _settings["model"] = env_model
    if env_base_url := os.environ.get("ANTHROPIC_BASE_URL"):
        _settings["base_url"] = env_base_url
    elif env_base_url := os.environ.get("NEXT_BASE_URL"):
        _settings["base_url"] = env_base_url


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


def update_setting(key: str, value: Any) -> None:
    """Update a setting value in memory and persist to project config.

    This is the public API for persisting setting changes — used by
    Store's onChange callback and PermissionManager instead of calling
    _save_json directly.
    """
    # Update in-memory settings
    _set_nested(_settings, key, value)
    # Persist to project config file
    _persist_to_project(key, value)


def get_all_settings() -> dict[str, Any]:
    """Return a snapshot of all settings."""
    return dict(_settings)


def show_config() -> None:
    """Print current configuration."""
    print(json.dumps(_settings, indent=2, ensure_ascii=False))


def is_persistable_key(key: str) -> bool:
    """Check if a key should be persisted when changed via Store."""
    return key in _PERSISTABLE_KEYS


def _set_nested(data: dict[str, Any], key: str, value: Any) -> None:
    """Set a value in a nested dict using dot-separated key."""
    parts = key.split(".")
    for part in parts[:-1]:
        if part not in data or not isinstance(data[part], dict):
            data[part] = {}
        data = data[part]
    data[parts[-1]] = value


def _persist_to_project(key: str, value: Any) -> None:
    """Persist a single key-value pair to the project .settings.json."""
    # Use project_root from Store if available, otherwise cwd
    from ..state.store import Store
    project_root = Store().get("project_root", str(Path.cwd()))
    project_path = Path(project_root) / ".nextcode" / ".settings.json"
    logger.info("Persisting %s to %s (project_root=%s)", key, project_path, project_root)
    data = _load_json(project_path)
    _set_nested(data, key, value)
    _save_json(project_path, data)


def _deep_merge(base: dict, override: dict) -> None:
    """Merge override into base, recursively for nested dicts."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
