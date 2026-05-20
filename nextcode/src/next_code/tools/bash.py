"""BashTool — shell command execution.

Enhanced with:
- Command semantic classification (search/read/list/silent)
- Exit code semantic interpretation (grep exit 1 != error)
- Security analysis (command substitution, dangerous builtins, etc.)
- Input validation (sleep detection)
- Read-only command auto-allow via whitelist
- Enhanced executor with large output persistence
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult
from .bash_executor import BashExecutor
from .bash_semantics import classify_pipeline, is_silent_command, CommandSemantic
from .bash_security import analyze_command_security
from .bash_input_validation import validate_bash_input
from .read_only_validation import check_read_only_constraints


class BashTool(Tool):
    name = "Bash"
    description = "Execute a bash command in the working directory."

    def __init__(self) -> None:
        super().__init__()
        self._executor = BashExecutor()

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
                "description": {
                    "type": "string",
                    "description": "Clear, concise description of what this command does",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "Set to true to run this command in the background",
                    "default": False,
                },
            },
            "required": ["command"],
        }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        """Check if this command is read-only (safe to auto-allow).

        Uses the read-only validation whitelist — checks that all
        sub-commands in the pipeline have only safe flags.
        FAIL-CLOSED: unknown commands or flags -> not read-only.
        """
        command = params.get("command", "")
        if not command:
            return False

        # Must also pass security checks
        security = analyze_command_security(command)
        if not security.is_safe:
            return False

        return check_read_only_constraints(command)

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        command = params["command"]
        timeout_ms = params.get("timeout", 120000)
        run_in_background = params.get("run_in_background", False)
        task_description = params.get("description", "")

        # Input validation (sleep detection, empty command, etc.)
        validation = validate_bash_input(command, run_in_background=run_in_background)
        if not validation.is_valid:
            return ToolResult(output=validation.message, error=True)

        # Security analysis
        security = analyze_command_security(command)
        if not security.is_safe:
            return ToolResult(
                output=f"Security check failed: {security.message}",
                error=True,
            )

        # Execute via enhanced executor
        result = await self._executor.execute(
            command,
            timeout_ms=timeout_ms,
            run_in_background=run_in_background,
            description=task_description,
        )

        # Format output
        output = self._format_result(result)
        return ToolResult(output=output, error=result.is_error)

    def _format_result(self, result: Any) -> str:
        """Format ExecResult for ToolResult output."""
        parts = []

        # Persisted output reference
        if result.output_file_path:
            return f'<persisted-output path="{result.output_file_path}">'

        # Semantic message (e.g., "No matches found" for grep exit 1)
        if result.semantic_message:
            parts.append(result.semantic_message)

        # stdout
        if result.stdout.strip():
            parts.append(result.stdout.rstrip())

        # stderr (only show for actual errors)
        if result.stderr.strip() and result.is_error:
            parts.append(result.stderr.rstrip())

        # Exit code (only for real errors)
        if result.is_error and result.exit_code != 0:
            parts.append(f"(exit code {result.exit_code})")

        output = "\n".join(parts)
        if not output:
            return "Done" if is_silent_command("") else ""
        return output
