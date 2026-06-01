"""TaskOutput tool — retrieve output from a background task."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult
from ..tasks.registry import get_task_registry
from ..tasks.types import TaskStatus


class TaskOutputTool(Tool):
    name = "TaskOutput"
    description = (
        "获取后台任务的输出结果。"
        "后台 Shell 任务完成时会自动通知并将结果注入对话，模型无需主动轮询。"
        "如果需要获取超过预览长度的完整输出，可使用此工具。"
        "如果任务还在运行中，返回当前状态和最新输出，不会阻塞等待。"
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
                "tail_lines": {
                    "type": "integer",
                    "default": 0,
                    "description": "只返回最后 N 行（0=返回全部），用于查看运行中任务的最新输出",
                },
            },
            "required": ["task_id"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        task_id = params.get("task_id", "")
        if not task_id:
            return ToolResult(output="需要提供 task_id 参数", error=True)

        registry = get_task_registry()
        task = registry.get(task_id)
        if task is None:
            return ToolResult(output=f"未找到任务: {task_id}", error=True)

        # If task is still running, return brief status without blocking
        if task.status == TaskStatus.RUNNING:
            desc = task.description or task.command[:80] if hasattr(task, 'command') else ""
            return ToolResult(output=f"任务 {task_id} 仍在运行中（{desc}），完成后会自动通知")

        # Task is terminal — gather output
        tail_lines = params.get("tail_lines", 0)
        return self._read_task_output(task, task_id, tail_lines=tail_lines)

    def _read_task_output(self, task: Any, task_id: str, tail_lines: int = 0) -> ToolResult:
        """Read task output from disk, with optional tail mode."""
        from ..tasks.disk_output import DiskTaskOutput

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
        return 10.0  # Quick — no blocking anymore
