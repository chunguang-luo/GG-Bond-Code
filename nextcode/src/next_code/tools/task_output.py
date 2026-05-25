"""TaskOutput tool — retrieve output from a background task."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Tool, ToolResult
from ..tasks.registry import get_task_registry
from ..tasks.types import TaskStatus
from ..tasks.disk_output import DiskTaskOutput


class TaskOutputTool(Tool):
    name = "TaskOutput"
    description = (
        "获取后台任务的输出结果。"
        "后台任务完成时会自动收到通知，收到通知后再用此工具获取结果。"
        "如果任务还在运行中，默认只返回当前状态，不会阻塞等待。"
        "使用 tail_lines 参数可以获取运行中任务的最新输出。"
    )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "要获取输出的后台任务 ID",
                },
                "block": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否阻塞等待任务完成（默认 False，不阻塞）。仅在极少数需要同步等待的场景设为 True。",
                },
                "timeout": {
                    "type": "integer",
                    "default": 30000,
                    "description": "阻塞等待超时时间（毫秒，默认 30000，即 30 秒），仅在 block=True 时生效",
                },
                "tail_lines": {
                    "type": "integer",
                    "default": 0,
                    "description": "只返回最后 N 行（0=返回全部），用于查看运行中任务的最新输出",
                },
            },
            "required": ["task_id"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        import asyncio

        task_id = params.get("task_id", "")
        if not task_id:
            return ToolResult(output="需要提供 task_id 参数", error=True)

        registry = get_task_registry()
        task = registry.get(task_id)
        if task is None:
            return ToolResult(output=f"未找到任务: {task_id}", error=True)

        # If task is still running and block=True, wait for it
        if task.status == TaskStatus.RUNNING:
            block = params.get("block", False)
            if not block:
                # Non-blocking: return current status immediately
                desc = task.description or task.command[:80] if hasattr(task, 'command') else ""
                return ToolResult(output=f"任务 {task_id} 仍在运行中（{desc}）。任务完成后会自动通知，届时再获取结果。")

            timeout_ms = params.get("timeout", 30000)
            timeout_s = timeout_ms / 1000

            # Poll until task reaches terminal state or timeout
            try:
                deadline = asyncio.get_event_loop().time() + timeout_s
                while asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(0.5)
                    task = registry.get(task_id)
                    if task is None or task.is_terminal():
                        break
            except asyncio.CancelledError:
                return ToolResult(output=f"等待被中断，任务 {task_id} 仍在运行")

            # Re-fetch after waiting
            task = registry.get(task_id)
            if task is None:
                return ToolResult(output=f"任务 {task_id} 已被移除", error=True)
            if task.status == TaskStatus.RUNNING:
                import time as _time
                elapsed = int(_time.monotonic() - task.started_at) if task.started_at else 0
                mins, secs = divmod(elapsed, 60)
                return ToolResult(output=f"任务 {task_id} 等待超时，仍在运行中 ({mins}m {secs}s)")

        # Task is terminal — gather output
        tail_lines = params.get("tail_lines", 0)
        return self._read_task_output(task, task_id, tail_lines=tail_lines)

    def _read_task_output(self, task: Any, task_id: str, tail_lines: int = 0) -> ToolResult:
        """Read task output from disk, with optional tail mode."""
        output_parts: list[str] = []

        # For bash tasks, use DiskTaskOutput for efficient tail reading
        if task._output_path:
            disk_output = DiskTaskOutput(task._output_path)
            if tail_lines > 0:
                content = disk_output.read_tail(lines=tail_lines)
            else:
                content = disk_output.read_all()

            if content.strip():
                output_parts.append(content)
            elif task.result:
                output_parts.append(task.result)
        elif task.result:
            # Agent task — result contains full text
            output_parts.append(task.result)

        output = "\n".join(output_parts)
        if not output.strip():
            return ToolResult(output="无输出")

        return ToolResult(output=output)

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True  # Only reads task state and output files

    def get_timeout(self) -> float:
        """TaskOutput may block-wait for task completion — needs longer timeout."""
        return 60.0  # 1 minute — block mode default is 30s
