"""Fallback detection — decide whether to use Ink or Rich REPL.

Checks:
1. Node.js availability (>= 18)
2. Frontend bundle existence
3. --ink flag value (off/auto/on)
4. Previous crash history (optional)

Returns an InkMode that determines the launch strategy.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class InkMode(str, Enum):
    """Ink frontend mode."""
    OFF = "off"      # Always use Rich REPL
    AUTO = "auto"    # Try Ink, fall back to Rich
    ON = "on"        # Require Ink, fail if unavailable


def resolve_ink_mode(flag_value: str | None = None) -> InkMode:
    """Resolve the effective Ink mode from CLI flag and environment.

    Priority:
    1. --ink CLI flag (off/auto/on)
    2. NEXTCODE_INK environment variable
    3. Default: AUTO (once Phase 6 is complete; OFF for now)
    """
    if flag_value is not None:
        try:
            return InkMode(flag_value.lower())
        except ValueError:
            logger.warning("Invalid --ink value: %s (expected off/auto/on)", flag_value)

    env_value = os.environ.get("NEXTCODE_INK", "").lower()
    if env_value in ("off", "auto", "on"):
        return InkMode(env_value)

    # Default: OFF until Phase 6 (Ink default)
    return InkMode.OFF


def check_ink_available() -> tuple[bool, str]:
    """Check if the Ink frontend can be launched.

    Returns (available, reason) tuple.
        available: True if Ink can be used
        reason: Human-readable explanation if unavailable
    """
    # 1. Check Node.js
    node_info = _check_node()
    if not node_info[0]:
        return node_info

    # 2. Check frontend bundle
    bundle_info = _check_bundle()
    if not bundle_info[0]:
        return bundle_info

    return True, "Ink frontend available"


def _check_node() -> tuple[bool, str]:
    """Check Node.js availability and version."""
    from .ink_launcher import _find_node

    node_path = _find_node()
    if node_path is None:
        return False, "Node.js not found in PATH (need >= 18)"

    # Check version
    try:
        result = subprocess.run(
            [node_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        version_str = result.stdout.strip()  # e.g., "v20.11.0"
        match = re.match(r"v(\d+)", version_str)
        if not match:
            return False, f"Cannot parse Node.js version: {version_str}"

        major = int(match.group(1))
        if major < 18:
            return False, f"Node.js {version_str} too old (need >= 18)"

        return True, f"Node.js {version_str} found at {node_path}"

    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"Cannot check Node.js version: {e}"


def _check_bundle() -> tuple[bool, str]:
    """Check frontend bundle existence."""
    from .ink_launcher import _find_frontend_bundle

    bundle = _find_frontend_bundle()
    if bundle is None:
        return False, "Ink frontend bundle not found (run 'npm run build' in frontend/)"

    return True, f"Bundle found at {bundle}"