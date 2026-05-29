"""Multi-layer settings — unified .settings.json config.

P0 features:
- Schema validation via Pydantic
- Array concatenation + dedup for merge (permissions rules)
- Security boundary (sensitive fields cannot be set from projectSettings)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from .settings_schema import (
    SettingsSchema,
    validate_settings,
    is_sensitive_field,
    check_security_boundary,
    safe_get_nested,
)

logger = logging.getLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "api_key": "",
    "model": "",
    "base_url": "",
    "api_protocol": "",
    "permissions": {
        "allow": [],
        "deny": [],
        "ask": [],
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

# Fields that should be merged as arrays (concatenate + dedup)
_ARRAY_MERGE_FIELDS: set[str] = {
    "permissions.allow",
    "permissions.deny",
    "permissions.ask",
    "permissions.hooks",
}


def _load_json(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Load a JSON file with validation.

    Returns:
        (data, warnings) — warnings contain parse errors, data may be partial.
    """
    warnings: list[str] = []
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            logger.warning("Invalid settings in %s: not a JSON object", path)
            warnings.append(f"Invalid settings in {path}: not a JSON object")
            return {}, warnings

        # Validate against schema
        is_valid, errors = validate_settings(data)
        if not is_valid:
            for error in errors:
                logger.warning("Settings validation error in %s: %s", path, error)
                warnings.append(f"{path}: {error}")
            # Continue with partial data

        return data, warnings
    except OSError as e:
        if not path.exists():
            # File not found is normal (e.g. first run, no global config) — debug only
            logger.debug("No settings file at %s", path)
        else:
            logger.warning("Failed to read settings from %s: %s", path, e)
            warnings.append(f"Failed to read {path}: {e}")
        return {}, warnings
    except json.JSONDecodeError as e:
        logger.error("Corrupted JSON in %s: %s", path, e)
        warnings.append(f"Corrupted JSON in {path}: {e}")
        return {}, warnings


def _save_json(path: Path, data: dict[str, Any]) -> None:
    """Save data to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_settings(*, source: str = "default") -> list[str]:
    """Load settings from global + project .settings.json files.

    Args:
        source: Identifier for the source being loaded (for logging).

    Returns:
        List of warning messages (non-fatal errors).
    """
    global _settings
    all_warnings: list[str] = []

    # Start with defaults
    _settings = _deep_copy(_DEFAULTS)

    # Layer 1: Global config (~/.nextcode/.settings.json)
    global_path = Path.home() / ".nextcode" / ".settings.json"
    if global_path.exists():
        global_data, warnings = _load_json(global_path)
        all_warnings.extend(warnings)
        if global_data:
            _deep_merge(_settings, global_data, source="global")

    # Layer 2: Project config (.nextcode/.settings.json)
    from ..state.store import Store
    project_root = Store().get("project_root", str(Path.cwd()))
    project_path = Path(project_root) / ".nextcode" / ".settings.json"
    if project_path.exists():
        project_data, warnings = _load_json(project_path)
        all_warnings.extend(warnings)
        if project_data:
            _deep_merge(_settings, project_data, source="project")

    # Layer 3: Apply env from settings file (these will be overridden by actual env vars)
    _apply_settings_env()

    # Layer 4: Environment variables override
    _apply_env_overrides()

    # Layer 5: Detect API protocol from base_url or model name
    _detect_api_protocol()

    # Print warnings for user attention
    if all_warnings:
        logger.warning("Settings loaded with %d warning(s): %s", len(all_warnings), all_warnings)

    return all_warnings


def _apply_env_overrides() -> None:
    """Apply os.environ to settings only when settings.env didn't already provide it.

    Priority: settings.env > os.environ
    """
    # Apply os.environ when the current setting is empty/falsy.
    # We can't use setdefault() because _DEFAULTS sets api_key="" etc.
    # and setdefault won't overwrite an existing (but empty) key.
    if not _settings.get("api_key"):
        if env_key := os.environ.get("NEXTCODE_API_KEY"):
            _settings["api_key"] = env_key
        elif env_key := os.environ.get("ANTHROPIC_API_KEY"):
            _settings["api_key"] = env_key
        elif env_key := os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            _settings["api_key"] = env_key

    if not _settings.get("model"):
        if env_model := os.environ.get("NEXTCODE_MODEL"):
            _settings["model"] = env_model
        elif env_model := os.environ.get("ANTHROPIC_MODEL"):
            _settings["model"] = env_model

    if not _settings.get("base_url"):
        if env_base_url := os.environ.get("ANTHROPIC_BASE_URL"):
            _settings["base_url"] = env_base_url
        elif env_base_url := os.environ.get("NEXT_BASE_URL"):
            _settings["base_url"] = env_base_url


def _apply_settings_env() -> None:
    """Apply env section from settings file to settings.

    These values have highest priority (override os.environ).
    Priority: settings.env > os.environ > defaults
    """
    env_config = _settings.get("env")
    if not env_config or not isinstance(env_config, dict):
        return

    # Map env keys to settings keys
    env_to_setting = {
        "ANTHROPIC_API_KEY": "api_key",
        "ANTHROPIC_MODEL": "model",
        "ANTHROPIC_BASE_URL": "base_url",
        "ANTHROPIC_AUTH_TOKEN": "api_key",
        "NEXTCODE_API_KEY": "api_key",
        "NEXTCODE_MODEL": "model",
        "NEXT_BASE_URL": "base_url",
    }

    for key, value in env_config.items():
        setting_key = env_to_setting.get(key)
        if setting_key:
            _settings[setting_key] = value
            logger.debug("Applied env from settings: %s -> %s", key, setting_key)


def _detect_api_protocol() -> None:
    """Detect API protocol based on base_url, env key prefixes, or model name.

    Priority:
    1. base_url contains "/anthropic" → anthropic
    2. env keys start with "ANTHROPIC_" → anthropic
    3. env keys start with "NEXTCODE_" → openai
    4. model name prefix (claude- → anthropic, deepseek- → openai, etc.)
    """
    base_url = _settings.get("base_url", "")

    # Priority 1: URL contains "/anthropic"
    if base_url and "/anthropic" in base_url:
        _settings["api_protocol"] = "anthropic"
        logger.debug("Detected /anthropic in base_url → api_protocol=anthropic")
        return

    # Priority 2 & 3: env key prefixes
    env_config = _settings.get("env")
    if env_config and isinstance(env_config, dict):
        has_anthropic_env = any(k.startswith("ANTHROPIC_") for k in env_config if env_config.get(k))
        has_openai_env = any(k.startswith("NEXTCODE_") for k in env_config if env_config.get(k))
        if has_anthropic_env and not has_openai_env:
            _settings["api_protocol"] = "anthropic"
            logger.debug("Detected ANTHROPIC_* env keys → api_protocol=anthropic")
            return
        if has_openai_env and not has_anthropic_env:
            _settings["api_protocol"] = "openai"
            logger.debug("Detected NEXTCODE_* env keys → api_protocol=openai")
            return

    # Priority 4: model name prefix
    model = _settings.get("model", "")
    family = _model_family_for_protocol(model)
    if family:
        _settings["api_protocol"] = family
        logger.debug("Inferred api_protocol=%s from model name '%s'", family, model)


# Model name prefixes for protocol detection (mirrors api/client.py)
_ANTHROPIC_PREFIXES = ("claude-", "minimax-")
_OPENAI_PREFIXES = (
    "deepseek-", "glm-", "gpt-", "o1-", "o3-", "o4-",
    "qwen-", "llama-", "gemini-", "mistral-", "yi-",
)


def _model_family_for_protocol(model: str) -> str | None:
    """Return 'anthropic' or 'openai' based on model name prefix."""
    lower = model.lower()
    for p in _ANTHROPIC_PREFIXES:
        if lower.startswith(p):
            return "anthropic"
    for p in _OPENAI_PREFIXES:
        if lower.startswith(p):
            return "openai"
    return None


def get_setting(key: str, default: Any = None) -> Any:
    """Get a setting value by dot-separated key."""
    return safe_get_nested(_settings, key, default)


def update_setting(
    key: str,
    value: Any,
    *,
    source: str = "project",
    check_security: bool = True,
) -> tuple[bool, str]:
    """Update a setting value in memory and persist to project config.

    Args:
        key: Dot-separated setting key (e.g. "permissions.allow").
        value: New value to set.
        source: Source identifier for security boundary check.
        check_security: Whether to check security boundary (skip for user/local sources).

    Returns:
        (success, message) — success is False if blocked by security boundary.
    """
    # Security boundary check
    if check_security and not check_security_boundary(key, source):
        msg = f"Cannot set '{key}' from {source}: security boundary violation"
        logger.error(msg)
        return False, msg

    # Update in-memory settings
    _set_nested(_settings, key, value)

    # Handle array merge for permission rules
    if key in _ARRAY_MERGE_FIELDS:
        # For array fields, we merge instead of replace
        existing = safe_get_nested(_settings, key, [])
        if isinstance(existing, list) and isinstance(value, list):
            # Dedupe + concat
            new_items = [v for v in value if v not in existing]
            merged = existing + new_items
            _set_nested(_settings, key, merged)

    # Persist to project config file
    _persist_to_project(key, _settings)

    return True, ""


def get_all_settings() -> dict[str, Any]:
    """Return a snapshot of all settings."""
    return _deep_copy(_settings)


def show_config() -> None:
    """Print current configuration."""
    print(json.dumps(_settings, indent=2, ensure_ascii=False))


def is_persistable_key(key: str) -> bool:
    """Check if a key should be persisted when changed via Store."""
    return key in _PERSISTABLE_KEYS


def _set_nested(data: dict[str, Any], key: str, value: Any) -> None:
    """Set a value in a nested dict using dot-separated key."""
    parts = key.split(".")
    d = data
    for part in parts[:-1]:
        if part not in d or not isinstance(d[part], dict):
            d[part] = {}
        d = d[part]
    d[parts[-1]] = value


def _deep_copy(data: dict[str, Any]) -> dict[str, Any]:
    """Deep copy a dict to avoid mutation."""
    import copy
    return copy.deepcopy(data)


def _deep_merge(base: dict[str, Any], override: dict[str, Any], *, source: str = "unknown") -> None:
    """Merge override into base with array concatenation for permission fields.

    Array fields (permissions.allow, permissions.deny, permissions.ask) are
    concatenated and deduplicated. All other fields use overwrite semantics.
    """
    for key, value in override.items():
        # Handle nested dicts recursively
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value, source=source)
        # Handle array merge fields
        elif key in _ARRAY_MERGE_FIELDS:
            base_key = key
            if isinstance(base.get(base_key), list) and isinstance(value, list):
                # Array concatenation + dedup
                existing = base[base_key]
                new_items = [v for v in value if v not in existing]
                base[base_key] = existing + new_items
            else:
                # Non-array value, overwrite
                base[key] = value
        # Handle SettingsSchema validation
        elif key == "permissions" and isinstance(value, dict):
            # Special handling for permissions: merge arrays
            for perm_key, perm_value in value.items():
                full_key = f"permissions.{perm_key}"
                if full_key in _ARRAY_MERGE_FIELDS:
                    if isinstance(base.get(key, {}).get(perm_key), list) and isinstance(perm_value, list):
                        existing = base[key].get(perm_key, [])
                        new_items = [v for v in perm_value if v not in existing]
                        base[key][perm_key] = existing + new_items
                    else:
                        base[key][perm_key] = perm_value
                else:
                    base[key][perm_key] = perm_value
        else:
            base[key] = value


def _persist_to_project(key: str, full_settings: dict[str, Any]) -> None:
    """Persist a single key-value pair to the project .settings.json."""
    from ..state.store import Store
    project_root = Store().get("project_root", str(Path.cwd()))
    project_path = Path(project_root) / ".nextcode" / ".settings.json"

    # Load existing data
    data, warnings = _load_json(project_path)
    if warnings:
        logger.warning("Persisting to file with warnings: %s", warnings)

    # Update the specific key
    _set_nested(data, key, safe_get_nested(full_settings, key))

    logger.info("Persisting %s to %s", key, project_path)
    _save_json(project_path, data)