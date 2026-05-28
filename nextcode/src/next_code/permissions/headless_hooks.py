"""Headless agent permission hooks — mirrors permissionRequest hooks.

When an agent runs in headless mode (no UI, no user to approve),
permission requests that would normally prompt the user must be handled
differently. This module provides TWO ways to register hooks:

1. settings.json configuration (recommended for users):
   ```json
   {
     "permissions": {
       "hooks": [
         {"command": "my-permission-script", "timeout": 5},
         {"command": "python3 /path/to/approval.py", "timeout": 10}
       ]
     }
   }
   ```
   Each hook receives a JSON request on stdin and must return a JSON
   decision on stdout:
     stdin:  {"toolName": "Bash", "params": {"command": "rm -rf /"}}
     stdout: {"decision": "deny", "reason": "rm is not allowed"}
   Valid decisions: "allow", "deny", "passthrough" (no opinion).

2. Python API (for programmatic integration):
   ```python
   from next_code.permissions.headless_hooks import register_permission_hook
   from next_code.permissions.manager import PermissionDecision

   async def my_hook(tool_name, params, context):
       if tool_name == "Bash" and "rm" in params.get("command", ""):
           return PermissionDecision.DENY
       return None

   register_permission_hook(my_hook)
   ```
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .manager import PermissionDecision

logger = logging.getLogger(__name__)

# ── Python API hooks ─────────────────────────────────────────────────────────

# Type alias for a permission request hook function.
PermissionRequestHook = Callable[
    [str, dict[str, Any], dict[str, Any]],
    Awaitable[PermissionDecision | None],
]

# Global registry of Python-level hooks
_python_hooks: list[PermissionRequestHook] = []


def register_permission_hook(hook: PermissionRequestHook) -> None:
    """Register a Python async permission hook for headless agents.

    Hooks are called in registration order. The first hook that returns
    a non-None decision wins. Use this for programmatic integration.

    Example:
        async def my_hook(tool_name, params, context):
            if tool_name == "Bash" and "rm" in params.get("command", ""):
                return PermissionDecision.DENY
            return None

        register_permission_hook(my_hook)
    """
    _python_hooks.append(hook)


def unregister_permission_hook(hook: PermissionRequestHook) -> None:
    """Remove a previously registered Python hook."""
    if hook in _python_hooks:
        _python_hooks.remove(hook)


def clear_permission_hooks() -> None:
    """Remove all registered hooks (both Python and config-based)."""
    _python_hooks.clear()


# ── Config-based hooks (settings.json) ───────────────────────────────────────


def _load_config_hooks() -> list[dict[str, Any]]:
    """Load permission hooks from settings.json."""
    from ..config.settings import get_setting
    hooks = get_setting("permissions.hooks", [])
    if not isinstance(hooks, list):
        return []
    return hooks


async def _run_config_hook(
    hook_config: dict[str, Any],
    tool_name: str,
    params: dict[str, Any],
) -> PermissionDecision | None:
    """Run a single config-based hook (external command).

    Sends a JSON request to the hook command via stdin and reads the
    JSON response from stdout. Stderr is captured for logging.

    Protocol:
      stdin:  {"toolName": "Bash", "params": {"command": "rm -rf /"}}
      stdout: {"decision": "allow|deny|passthrough", "reason": "..."}

    Returns None if the hook times out, fails, or returns "passthrough".
    """
    command = hook_config.get("command", "")
    if not command:
        return None

    timeout = hook_config.get("timeout", 5)

    request = json.dumps({
        "toolName": tool_name,
        "params": params,
    })

    try:
        # Use shell=True so users can write commands like "python3 -c '...'"
        # or "/path/to/script.sh" without splitting args manually.
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(request.encode()),
            timeout=timeout,
        )

        if stderr_bytes:
            logger.warning(
                "Permission hook stderr (command=%s): %s",
                command, stderr_bytes.decode()[:500],
            )

        if proc.returncode != 0:
            logger.warning(
                "Permission hook exited with code %d (command=%s)",
                proc.returncode, command,
            )
            return None

        response = json.loads(stdout_bytes.decode())
        decision_str = response.get("decision", "passthrough")

        if decision_str == "allow":
            return PermissionDecision.ALLOW
        elif decision_str == "deny":
            reason = response.get("reason", "")
            logger.info("Hook denied: %s (command=%s)", reason, command)
            return PermissionDecision.DENY
        else:
            # "passthrough" or unknown — no opinion
            return None

    except asyncio.TimeoutError:
        logger.warning(
            "Permission hook timed out after %ds (command=%s)",
            timeout, command,
        )
        return None
    except json.JSONDecodeError:
        logger.warning(
            "Permission hook returned invalid JSON (command=%s): %s",
            command, stdout_bytes.decode()[:200] if stdout_bytes else "<empty>",
        )
        return None
    except FileNotFoundError:
        logger.warning(
            "Permission hook command not found: %s", command,
        )
        return None
    except Exception:
        logger.exception(
            "Permission hook error (command=%s)", command,
        )
        return None


# ── Unified hook runner ──────────────────────────────────────────────────────


async def run_permission_hooks(
    tool_name: str,
    params: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> PermissionDecision | None:
    """Run all registered hooks and return the first decision.

    Order of execution:
    1. Config-based hooks (from settings.json permissions.hooks), in order
    2. Python API hooks (registered via register_permission_hook()), in order

    The first hook that returns a non-None decision wins.

    Args:
        tool_name: The name of the tool requesting permission.
        params: Tool parameters.
        context: Optional context dict (e.g. agent metadata).

    Returns:
        The first non-None decision from a hook, or None if all hooks
        return None (meaning "no opinion, use default behavior").
    """
    if context is None:
        context = {}

    # 1. Run config-based hooks first (user-configured, higher priority)
    config_hooks = _load_config_hooks()
    for hook_config in config_hooks:
        try:
            result = await _run_config_hook(hook_config, tool_name, params)
            if result is not None:
                return result
        except Exception:
            logger.exception("Config hook error for tool=%s", tool_name)

    # 2. Run Python API hooks
    for hook in _python_hooks:
        try:
            result = await hook(tool_name, params, context)
            if result is not None:
                return result
        except Exception:
            logger.exception("Python hook error for tool=%s", tool_name)

    return None


def get_hook_count() -> int:
    """Return the number of registered hooks (useful for testing)."""
    return len(_load_config_hooks()) + len(_python_hooks)