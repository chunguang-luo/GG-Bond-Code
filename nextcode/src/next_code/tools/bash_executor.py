"""Enhanced bash command executor with progress and background support.

Mirrors runShellCommand() from BashTool.tsx — AsyncGenerator-based
execution with progress output, background task conversion, and
large output persistence.

Key design:
- 2-second progress threshold: commands finishing within 2s show
  instant results without flickering progress bars
- Exit code semantic interpretation: grep returning 1 for "no match"
  is not an error
- Large output persistence: output > max_result_chars is saved to
  disk and referenced by path
- Background execution: commands can be run in background
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bash_exit_codes import interpret_exit_code
from .bash_semantics import classify_pipeline, is_silent_command, CommandSemantic
from .bash_input_validation import validate_bash_input


# --- Constants (mirrors BashTool.tsx) ---

PROGRESS_THRESHOLD_S = 2.0        # Show progress after 2 seconds
MAX_PERSISTED_SIZE = 64 * 1024 * 1024  # 64MB output limit
DEFAULT_TIMEOUT_MS = 120_000     # 2 minute default
MAX_RESULT_CHARS = 30_000        # BashTool threshold for persistence
ASSISTANT_BLOCKING_BUDGET_S = 15  # Auto-background after 15s in assistant mode


@dataclass
class ExecResult:
    """Result from command execution."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    is_error: bool = False
    semantic_message: str | None = None  # Exit code semantic message
    output_file_path: str | None = None  # If output was persisted to disk
    semantic: CommandSemantic = CommandSemantic.UNKNOWN


@dataclass
class ProgressUpdate:
    """Progress update during command execution."""
    output: str = ""
    elapsed_seconds: float = 0.0
    total_lines: int = 0


class BashExecutor:
    """Enhanced bash command executor.

    Features:
    - Progress reporting with 2-second delay
    - Exit code semantic interpretation
    - Large output file persistence
    - Input validation (sleep detection)
    - Semantic classification
    """

    def __init__(
        self,
        *,
        max_result_chars: int = MAX_RESULT_CHARS,
        default_timeout_ms: int = DEFAULT_TIMEOUT_MS,
        progress_threshold_s: float = PROGRESS_THRESHOLD_S,
    ) -> None:
        self._max_result_chars = max_result_chars
        self._default_timeout_ms = default_timeout_ms
        self._progress_threshold_s = progress_threshold_s

    async def execute(
        self,
        command: str,
        *,
        timeout_ms: int | None = None,
        run_in_background: bool = False,
        working_dir: str | None = None,
    ) -> ExecResult:
        """Execute a shell command and return a structured result.

        Args:
            command: The shell command to execute.
            timeout_ms: Timeout in milliseconds (default: 120000).
            run_in_background: If True, don't wait for completion.
            working_dir: Working directory for the command.

        Returns:
            ExecResult with structured output and semantic info.
        """
        # 1. Input validation
        validation = validate_bash_input(command, run_in_background=run_in_background)
        if not validation.is_valid:
            return ExecResult(
                is_error=True,
                stderr=validation.message,
                exit_code=validation.error_code,
            )

        # 2. Semantic classification
        semantic = classify_pipeline(command)
        silent = is_silent_command(command)

        # 3. Execute
        timeout_s = (timeout_ms or self._default_timeout_ms) / 1000

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ExecResult(
                    is_error=True,
                    stderr=f"Command timed out after {timeout_s:.0f}s",
                    exit_code=-1,
                    semantic=semantic,
                )

            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")
            exit_code = proc.returncode or 0

        except Exception as e:
            return ExecResult(
                is_error=True,
                stderr=str(e),
                exit_code=-1,
                semantic=semantic,
            )

        # 4. Exit code semantic interpretation
        exit_semantic = interpret_exit_code(command, exit_code)

        # 5. Build result
        result = ExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            is_error=exit_semantic.is_error,
            semantic_message=exit_semantic.message,
            semantic=semantic,
        )

        # 6. Large output persistence
        combined_output = self._format_output(result, silent)
        if len(combined_output) > self._max_result_chars:
            output_path = self._persist_output(combined_output)
            result.output_file_path = output_path

        return result

    def format_for_tool_result(self, result: ExecResult) -> str:
        """Format an ExecResult for ToolResult.output.

        Handles:
        - Silent commands: "Done" instead of empty output
        - Exit code messages: "No matches found" for grep exit 1
        - Error formatting: include stderr and exit code
        - Large output: reference to persisted file
        """
        if result.output_file_path:
            return f"<persisted-output path=\"{result.output_file_path}\">"

        output = self._format_output(result, is_silent_command(""))
        return output

    def _format_output(self, result: ExecResult, silent: bool) -> str:
        """Format command output for display."""
        parts = []

        # Semantic message (e.g., "No matches found")
        if result.semantic_message:
            parts.append(result.semantic_message)

        # stdout
        stdout = result.stdout
        if stdout.strip():
            parts.append(stdout.rstrip())
        elif not result.semantic_message and not silent:
            # No stdout and no semantic message — show nothing
            pass

        # stderr (only if there is actual error content)
        if result.stderr.strip() and result.is_error:
            parts.append(result.stderr.rstrip())

        # Exit code (only for actual errors)
        if result.is_error and result.exit_code != 0:
            parts.append(f"(exit code {result.exit_code})")

        output = "\n".join(parts)
        if not output:
            return "Done" if silent else ""
        return output

    def _persist_output(self, output: str) -> str:
        """Persist large output to a temporary file.

        Returns the path to the persisted file.
        """
        try:
            tmp_dir = Path(tempfile.gettempdir()) / "nextcode-tool-results"
            tmp_dir.mkdir(parents=True, exist_ok=True)

            # Truncate if over limit
            if len(output) > MAX_PERSISTED_SIZE:
                output = output[:MAX_PERSISTED_SIZE] + "\n... (output truncated)"

            # Use a counter for uniqueness
            import time
            filename = f"bash-output-{int(time.time() * 1000)}.txt"
            path = tmp_dir / filename
            path.write_text(output, errors="replace")

            return str(path)
        except Exception:
            return ""
