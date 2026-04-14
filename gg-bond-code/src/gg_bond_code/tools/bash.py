"""BashTool — shell command execution."""

from __future__ import annotations

import asyncio
from typing import Any

from .base import Tool, ToolResult


class BashTool(Tool):
    name = "Bash"
    description = "Execute a bash command in the working directory."

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in milliseconds (default 120000)",
                    "default": 120000,
                },
            },
            "required": ["command"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        command = params["command"]
        timeout_ms = params.get("timeout", 120000)
        timeout_s = timeout_ms / 1000

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            output = stdout.decode(errors="replace")
            if proc.returncode != 0:
                err = stderr.decode(errors="replace")
                return ToolResult(output=f"Exit code {proc.returncode}\n{output}\n{err}", error=True)
            return ToolResult(output=output)
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            return ToolResult(output=f"Command timed out after {timeout_ms}ms", error=True)
        except Exception as e:
            return ToolResult(output=str(e), error=True)
