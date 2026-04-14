"""FileWriteTool — write content to a file."""

from __future__ import annotations

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
        file_path = Path(params["file_path"])
        content = params["content"]

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)
            return ToolResult(output=f"Wrote {len(content)} bytes to {file_path}")
        except Exception as e:
            return ToolResult(output=str(e), error=True)
