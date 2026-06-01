"""headersHelper — dynamic auth header injection via shell scripts.

For MCP servers that don't use OAuth, headersHelper provides a mechanism
to inject dynamic authentication headers by executing a shell script each
time a connection is established.

Why this exists:
Some enterprise MCP servers use custom auth mechanisms (API keys, JWTs from
internal IdPs, etc.) that don't fit the standard OAuth flow. headersHelper
lets users write a script that outputs the required headers as JSON, similar
to git credential-helper.

Security:
- Scripts from project/local scope require workspace trust confirmation.
- Scripts run with a 10-second timeout.
- Scripts inherit the full environment plus MCP-specific variables.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Script execution timeout
HEADERS_HELPER_TIMEOUT_S = 10.0


async def execute_headers_helper(
    script_path: str,
    server_name: str,
    server_url: str,
    *,
    cwd: str | None = None,
) -> dict[str, str]:
    """Execute a headersHelper script and return the parsed headers.

    Args:
        script_path: Path to the script to execute.
        server_name: MCP server name (passed as env var).
        server_url: MCP server URL (passed as env var).
        cwd: Working directory for script execution.

    Returns:
        Dict of header name → value pairs.

    Raises:
        RuntimeError: If the script fails or returns invalid output.
    """
    if not script_path:
        return {}

    # Build environment
    env = dict(os.environ)
    env["CLAUDE_CODE_MCP_SERVER_NAME"] = server_name
    env["CLAUDE_CODE_MCP_SERVER_URL"] = server_url

    try:
        proc = await asyncio.create_subprocess_exec(
            script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=HEADERS_HELPER_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"headersHelper script timed out after {HEADERS_HELPER_TIMEOUT_S}s: {script_path}"
            )

        if proc.returncode != 0:
            stderr_text = stderr.decode().strip() if stderr else ""
            raise RuntimeError(
                f"headersHelper script failed (exit {proc.returncode}): {stderr_text}"
            )

        # Parse JSON output
        output = stdout.decode().strip()
        if not output:
            return {}

        try:
            headers = json.loads(output)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"headersHelper script returned invalid JSON: {e}\nOutput: {output[:200]}"
            )

        if not isinstance(headers, dict):
            raise RuntimeError(
                f"headersHelper script must return a JSON object, got {type(headers).__name__}"
            )

        # Validate all values are strings
        result: dict[str, str] = {}
        for key, value in headers.items():
            if not isinstance(key, str):
                continue
            result[key] = str(value)

        logger.debug("headersHelper for '%s' returned %d headers", server_name, len(result))
        return result

    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Failed to execute headersHelper script: {e}") from e


def should_run_headers_helper(
    headers_helper: str,
    scope: str,
    *,
    is_workspace_trusted: bool | None = None,
) -> bool:
    """Check if a headersHelper script should be executed.

    Security gate: scripts from project/local scope require workspace trust.
    Returns True if safe to run, False if blocked.
    """
    if not headers_helper:
        return False

    # User and dynamic scope scripts are always allowed
    if scope in ("user", "dynamic", "enterprise"):
        return True

    # Project and local scope require workspace trust
    if scope in ("project", "local"):
        if is_workspace_trusted is False:
            logger.warning(
                "Skipping headersHelper for project-scoped server (workspace not trusted)"
            )
            return False
        return True

    return True
