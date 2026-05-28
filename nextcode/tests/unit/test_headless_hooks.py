"""Tests for permissions/headless_hooks.py — permission request hooks.

Tests both:
- Config-based hooks (external command via stdin/stdout JSON protocol)
- Python API hooks (programmatic async functions)
"""

import json
import pytest

from next_code.permissions.manager import PermissionDecision
from next_code.permissions.headless_hooks import (
    register_permission_hook,
    unregister_permission_hook,
    clear_permission_hooks,
    run_permission_hooks,
    _run_config_hook,
    get_hook_count,
)


@pytest.fixture(autouse=True)
def clean_hooks():
    """Clean up hooks before and after each test."""
    clear_permission_hooks()
    yield
    clear_permission_hooks()


# ── Config-based hook tests ──────────────────────────────────────────────────


class TestConfigHook:
    @pytest.mark.asyncio
    async def test_hook_allows(self):
        """Hook that returns allow decision via stdout JSON."""
        hook_config = {
            "command": f"python3 -c \"import sys,json; "
                       f"req=json.load(sys.stdin); "
                       f"print(json.dumps({{'decision': 'allow'}}))\"",
            "timeout": 5,
        }
        result = await _run_config_hook(hook_config, "Bash", {"command": "ls"})
        assert result == PermissionDecision.ALLOW

    @pytest.mark.asyncio
    async def test_hook_denies(self):
        """Hook that returns deny decision."""
        hook_config = {
            "command": f"python3 -c \"import sys,json; "
                       f"print(json.dumps({{'decision': 'deny', 'reason': 'not allowed'}}))\"",
            "timeout": 5,
        }
        result = await _run_config_hook(hook_config, "Bash", {"command": "rm -rf /"})
        assert result == PermissionDecision.DENY

    @pytest.mark.asyncio
    async def test_hook_passthrough(self):
        """Hook that returns passthrough (no opinion)."""
        hook_config = {
            "command": f"python3 -c \"import sys,json; "
                       f"print(json.dumps({{'decision': 'passthrough'}}))\"",
            "timeout": 5,
        }
        result = await _run_config_hook(hook_config, "Bash", {"command": "ls"})
        assert result is None

    @pytest.mark.asyncio
    async def test_hook_receives_correct_input(self):
        """Hook receives toolName and params via stdin JSON."""
        # Create a script that echoes back the input for verification
        script = (
            "import sys,json; "
            "req=json.load(sys.stdin); "
            "assert req['toolName']=='Bash'; "
            "assert req['params']['command']=='git push'; "
            "print(json.dumps({'decision': 'allow'}))"
        )
        hook_config = {
            "command": f"python3 -c \"{script}\"",
            "timeout": 5,
        }
        result = await _run_config_hook(hook_config, "Bash", {"command": "git push"})
        assert result == PermissionDecision.ALLOW

    @pytest.mark.asyncio
    async def test_hook_command_not_found(self):
        """Non-existent command returns None (graceful degradation)."""
        hook_config = {
            "command": "/nonexistent/path/to/hook",
            "timeout": 1,
        }
        result = await _run_config_hook(hook_config, "Bash", {"command": "ls"})
        assert result is None

    @pytest.mark.asyncio
    async def test_hook_timeout(self):
        """Hook that takes too long returns None."""
        hook_config = {
            "command": "sleep 10",
            "timeout": 0.5,
        }
        result = await _run_config_hook(hook_config, "Bash", {"command": "ls"})
        assert result is None

    @pytest.mark.asyncio
    async def test_hook_invalid_json(self):
        """Hook that returns invalid JSON returns None."""
        hook_config = {
            "command": "echo 'not valid json'",
            "timeout": 5,
        }
        result = await _run_config_hook(hook_config, "Bash", {"command": "ls"})
        assert result is None

    @pytest.mark.asyncio
    async def test_hook_missing_decision_field(self):
        """Hook returning JSON without 'decision' field defaults to passthrough."""
        hook_config = {
            "command": f"python3 -c \"import json; print(json.dumps({{'other': 'value'}}))\"",
            "timeout": 5,
        }
        result = await _run_config_hook(hook_config, "Bash", {"command": "ls"})
        assert result is None


# ── Python API hook tests ────────────────────────────────────────────────────


class TestPythonHook:
    @pytest.mark.asyncio
    async def test_register_and_run(self):
        async def my_hook(tool_name, params, context):
            if tool_name == "Bash":
                return PermissionDecision.DENY
            return None

        register_permission_hook(my_hook)
        assert get_hook_count() == 1

        result = await run_permission_hooks("Bash", {"command": "rm -rf"})
        assert result == PermissionDecision.DENY

    @pytest.mark.asyncio
    async def test_hook_returns_none(self):
        async def no_opinion(tool_name, params, context):
            return None

        register_permission_hook(no_opinion)
        result = await run_permission_hooks("Bash", {"command": "ls"})
        assert result is None

    @pytest.mark.asyncio
    async def test_first_hook_wins(self):
        async def hook1(tool_name, params, context):
            return PermissionDecision.ALLOW

        async def hook2(tool_name, params, context):
            return PermissionDecision.DENY

        register_permission_hook(hook1)
        register_permission_hook(hook2)

        result = await run_permission_hooks("Bash", {"command": "rm"})
        assert result == PermissionDecision.ALLOW

    @pytest.mark.asyncio
    async def test_hook_receives_context(self):
        received_context = None

        async def my_hook(tool_name, params, context):
            nonlocal received_context
            received_context = context
            return None

        register_permission_hook(my_hook)
        ctx = {"agent_id": "test-123", "agent_type": "general"}
        await run_permission_hooks("Edit", {"file_path": "/tmp/test"}, context=ctx)
        assert received_context == ctx

    @pytest.mark.asyncio
    async def test_hook_error_does_not_crash(self):
        async def bad_hook(tool_name, params, context):
            raise RuntimeError("Simulated hook failure")

        async def good_hook(tool_name, params, context):
            return PermissionDecision.ALLOW

        register_permission_hook(bad_hook)
        register_permission_hook(good_hook)

        result = await run_permission_hooks("Bash", {"command": "ls"})
        assert result == PermissionDecision.ALLOW


class TestHookUnregistration:
    @pytest.mark.asyncio
    async def test_unregister(self):
        async def my_hook(tool_name, params, context):
            return PermissionDecision.DENY

        register_permission_hook(my_hook)
        assert get_hook_count() == 1

        unregister_permission_hook(my_hook)
        assert get_hook_count() == 0

    @pytest.mark.asyncio
    async def test_unregister_nonexistent(self):
        async def my_hook(tool_name, params, context):
            return PermissionDecision.DENY

        unregister_permission_hook(my_hook)
        assert get_hook_count() == 0

    @pytest.mark.asyncio
    async def test_clear_all(self):
        async def hook1(tool_name, params, context):
            return None

        async def hook2(tool_name, params, context):
            return None

        register_permission_hook(hook1)
        register_permission_hook(hook2)
        assert get_hook_count() == 2

        clear_permission_hooks()
        assert get_hook_count() == 0


# ── Config hook priority tests ───────────────────────────────────────────────


class TestConfigHookPriority:
    """Config-based hooks run BEFORE Python hooks (user config > programmatic)."""

    @pytest.mark.asyncio
    async def test_both_hook_types_work_independently(self):
        """Config hooks and Python hooks both work correctly."""
        async def python_hook(tool_name, params, context):
            return PermissionDecision.ALLOW

        register_permission_hook(python_hook)

        # Config hook returns deny
        hook_config = {
            "command": f"python3 -c \"import json; "
                       f"print(json.dumps({{'decision': 'deny'}}))\"",
            "timeout": 5,
        }
        result = await _run_config_hook(hook_config, "Bash", {"command": "ls"})
        assert result == PermissionDecision.DENY

        # Python hook returns allow (when run directly without config hooks)
        result2 = await run_permission_hooks("Bash", {"command": "ls"})
        # Config hooks come from settings (empty in tests), so Python hook wins
        assert result2 == PermissionDecision.ALLOW