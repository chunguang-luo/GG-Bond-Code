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
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bash_exit_codes import interpret_exit_code
from .bash_semantics import classify_pipeline, is_silent_command, CommandSemantic
from .bash_input_validation import validate_bash_input
from ..tasks.types import TaskStateBase, TaskType, TaskStatus, generate_task_id
from ..tasks.registry import get_task_registry
from ..tasks.disk_output import DiskTaskOutput
from ..tasks.stall_watchdog import start_watchdog, stop_watchdog, record_task_output

logger = logging.getLogger(__name__)


# --- Stall watchdog callback ─────────────────────────────────────────────────


def _create_stall_callback() -> callable:
    """Create the stall notification callback for the IPC bridge.

    When a stall is detected, this sends a notification to the frontend
    so the model can decide whether to kill or handle the stalled task.
    """
    # Get the IPC bridge's emit function from the global context
    # This is a lazy import to avoid circular dependencies
    def on_stall_detected(task_id: str, prompt_type: str) -> None:
        from ..ipc.bridge import get_stall_notification_callback
        callback = get_stall_notification_callback()
        if callback:
            try:
                # Run in the event loop to avoid blocking
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(callback(task_id, prompt_type))
                else:
                    loop.run_until_complete(callback(task_id, prompt_type))
            except Exception:
                logger.exception("Failed to send stall notification for %s", task_id)

    return on_stall_detected


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
        description: str = "",
    ) -> ExecResult:
        """Execute a shell command and return a structured result.

        Args:
            command: The shell command to execute.
            timeout_ms: Timeout in milliseconds (default: 120000).
            run_in_background: If True, don't wait for completion.
            working_dir: Working directory for the command.
            description: Short description for background task display.

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

        # 2b. Background execution
        if run_in_background:
            return await self._execute_background(
                command, working_dir=working_dir, semantic=semantic,
                description=description,
            )

        # 3. Execute (foreground)
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
            except asyncio.CancelledError:
                # User interrupt (Esc) — kill the subprocess immediately
                proc.kill()
                await proc.wait()
                raise  # Re-raise so the query task is properly cancelled

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

    async def _execute_background(
        self,
        command: str,
        *,
        working_dir: str | None = None,
        semantic: CommandSemantic = CommandSemantic.UNKNOWN,
        description: str = "",
    ) -> ExecResult:
        """Execute a command in the background — register in TaskRegistry, return immediately.

        The command runs asynchronously. The main query loop will wait for
        all background tasks to complete before finishing.
        """
        registry = get_task_registry()
        task_id = generate_task_id(TaskType.LOCAL_BASH)

        # Create output directory and path
        output_dir = Path(tempfile.gettempdir()) / "nextcode-tasks"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"{task_id}.output")

        # Create task state
        task = TaskStateBase(
            id=task_id,
            type=TaskType.LOCAL_BASH,
            status=TaskStatus.RUNNING,
            command=command,
            description=description[:50] if description else "",
            started_at=time.monotonic(),
            _output_path=output_path,
        )
        registry.register(task)

        # Create DiskTaskOutput for streaming output to disk
        disk_output = DiskTaskOutput(output_path)

        # Start the subprocess with piped stdout/stderr
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
            )
        except Exception as e:
            registry.update(task_id, status=TaskStatus.FAILED, result=str(e))
            return ExecResult(is_error=True, stderr=str(e), exit_code=-1, semantic=semantic)

        # Store process and disk_output handles for kill support
        task._process = proc
        task._disk_output = disk_output  # type: ignore[attr-defined]
        registry.update(task_id)

        # Start stall watchdog to detect interactive prompts (e.g., apt/yum confirmation)
        stall_callback = _create_stall_callback()
        start_watchdog(task_id, output_path, stall_callback)

        # Fire-and-forget: stream output to disk when process finishes
        async def _background_wait() -> None:
            try:
                # Read output from subprocess
                stdout_bytes, stderr_bytes = await proc.communicate()
                exit_code = proc.returncode or 0

                stdout = stdout_bytes.decode(errors="replace")
                stderr = stderr_bytes.decode(errors="replace")

                # Append output to disk via DiskTaskOutput (queued write)
                disk_output.append(f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}\n\nEXIT CODE: {exit_code}")
                await disk_output.flush()

                # Update task state
                status = TaskStatus.COMPLETED if exit_code == 0 else TaskStatus.FAILED
                result_preview = stdout[:500] if stdout else stderr[:500]
                registry.update(task_id, status=status, result=result_preview)

                logger.info("Background task %s finished with exit code %d", task_id, exit_code)

            except asyncio.CancelledError:
                registry.update(task_id, status=TaskStatus.KILLED, result="Cancelled")
            except Exception as e:
                logger.exception("Background task %s failed", task_id)
                registry.update(task_id, status=TaskStatus.FAILED, result=str(e))
            finally:
                # Stop the stall watchdog when task completes
                stop_watchdog(task_id)

        aio_task = asyncio.create_task(_background_wait())
        task._asyncio_task = aio_task
        registry.update(task_id)

        return ExecResult(
            stdout=f"Background task started: {task_id}",
            stderr="",
            exit_code=0,
            is_error=False,
            semantic=semantic,
        )
