"""TaskStop tool — kill a running background task."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult
from ..tasks.registry import get_task_registry
from ..tasks.types import TaskStatus


class TaskStopTool(Tool):
    name = "TaskStop"
    description = "终止一个后台任务。"

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "要终止的后台任务 ID（以 'b' 或 'a' 开头）",
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

        if task.is_terminal():
            return ToolResult(output=f"任务 {task_id} 已经处于终态 ({task.status.value})")

        killed = await registry.kill(task_id)
        if killed:
            return ToolResult(output=f"任务 {task_id} 已终止")
        else:
            return ToolResult(output=f"无法终止任务 {task_id}", error=True)

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True  # Only modifies task state, not files
