"""Ink frontend availability check.

Checks:
1. Node.js availability (>= 18)
2. Frontend bundle existence

Used as a pre-flight check before launching the Ink frontend.
"""

from __future__ import annotations

import logging
import re
import subprocess

logger = logging.getLogger(__name__)


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
