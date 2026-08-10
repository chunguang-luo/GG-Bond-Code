"""FileWriteTool — write content to a file."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult


class FileWriteTool(Tool):
    name = "Write"
    description = "Write content to a file, creating it if it doesn't exist."

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write",
                },
            },
            "required": ["file_path", "content"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        file_path_str = params.get("file_path")
        content = params.get("content")
        if not file_path_str:
            return ToolResult(
                output="Write 工具缺少 file_path 参数，请确保提供了要写入的文件绝对路径",
                error=True,
            )
        if content is None:
            return ToolResult(
                output="Write 工具缺少 content 参数，请确保提供了要写入的内容",
                error=True,
            )
        file_path = Path(file_path_str)

        try:
            # Compute diff if file exists
            metadata: dict[str, Any] = {}
            if file_path.exists():
                old_content = file_path.read_text()
                old_lines = old_content.splitlines(keepends=True)
                new_lines = content.splitlines(keepends=True)
                diff = difflib.unified_diff(
                    old_lines, new_lines,
                    fromfile="", tofile="",
                    n=1,
                )
                diff_lines = list(diff)
                added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
                removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
                # Remove --- and +++ header lines (file path is already in tool label)
                diff_lines = [l for l in diff_lines if not l.startswith("---") and not l.startswith("+++")]
                if len(diff_lines) > 15:
                    diff_lines = diff_lines[:15]
                metadata = {"added": added, "removed": removed, "diff": "\n".join(diff_lines)}
            else:
                # New file — count all lines as added
                new_lines = content.splitlines(keepends=True)
                metadata = {"added": len(new_lines), "removed": 0, "diff": ""}

            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)

            # Update file cache after successful write
            if self._context is not None:
                try:
                    self._context.file_cache.record_write(str(file_path), content)
                except Exception:
                    pass  # Cache update is best-effort

            return ToolResult(output="Written", metadata=metadata)
        except Exception as e:
            return ToolResult(output=str(e), error=True)
