"""Settings Schema — Pydantic validation for configuration files.

Provides type-safe validation for .settings.json files with:
- Field type validation
- Array concatenation + dedup for merge
- Security boundary for sensitive fields
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class PermissionsSettings(BaseModel):
    """Permissions configuration schema."""
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)


class ContextSettings(BaseModel):
    """Context/compaction settings schema."""
    max_tokens: int = Field(default=65536, ge=1024, le=1048576)
    compact_threshold: float = Field(default=0.8, ge=0.1, le=0.99)
    auto_compact_buffer: int = Field(default=13000, ge=0)
    blocking_buffer: int = Field(default=3000, ge=0)
    circuit_breaker_max_failures: int = Field(default=3, ge=1, le=100)
    microcompact_keep_recent: int = Field(default=3, ge=0)


class SettingsSchema(BaseModel):
    """Root settings schema with validation."""
    api_key: str = Field(default="")
    model: str = Field(default="")
    base_url: str = Field(default="")
    permissions: PermissionsSettings = Field(default_factory=PermissionsSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)

    model_config = {"extra": "allow"}  # Unknown fields are allowed (forward compatibility)

    @field_validator("api_key", "model", "base_url", mode="before")
    @classmethod
    def validate_string_fields(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)


def validate_settings(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate settings data against schema.

    Returns:
        (is_valid, error_messages) — errors are non-fatal, partial data is still used.
    """
    errors: list[str] = []
    validated: dict[str, Any] = {}

    try:
        SettingsSchema.model_validate(data)
    except Exception as e:
        errors.append(str(e))

    return len(errors) == 0, errors


def safe_get_nested(data: dict, key: str, default: Any = None) -> Any:
    """Safely get nested dict value by dot-separated key."""
    parts = key.split(".")
    value = data
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return default
    return value


# Fields that cannot be set from projectSettings (Git-trusted sources)
# These fields can only be set from userSettings, localSettings, flagSettings, or policySettings
_SENSITIVE_FIELDS: set[str] = {
    "permissions.bypass_all",
    "permissions.skip_dangerous_mode_prompt",
    "sandbox.enabled",
}

# Settings sources in priority order (higher index = higher priority)
_SETTINGS_SOURCES = [
    "defaults",
    "global",      # ~/.nextcode/.settings.json
    "project",     # .nextcode/.settings.json
    "local",       # .nextcode/.settings.local.json (future)
    "flag",        # --settings CLI parameter (future)
    "policy",      # MDM / managed-settings (future)
    "env",         # Environment variables
]


def is_sensitive_field(key: str) -> bool:
    """Check if a field is sensitive (cannot be set from projectSettings)."""
    return key in _SENSITIVE_FIELDS


def is_project_source(source: str) -> bool:
    """Check if a source is considered untrusted (Git-based)."""
    return source == "project"


def check_security_boundary(key: str, source: str) -> bool:
    """Check if setting this field from this source is allowed.

    Returns True if allowed, False if blocked by security boundary.
    """
    if not is_sensitive_field(key):
        return True  # Non-sensitive fields are always allowed

    if is_project_source(source):
        return False  # Project settings cannot set sensitive fields

    return True