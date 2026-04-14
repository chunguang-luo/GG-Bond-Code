"""FileReadTool — read file contents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gg_bond_code.tools.base import Tool, ToolResult


class FileReadTool(Tool):
    name = "Read"
    description = "Read the contents of a file."

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of lines to read",
                },
            },
            "required": ["file_path"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        file_path = Path(params["file_path"])
        offset = params.get("offset")
        limit = params.get("limit")

        if not file_path.exists():
            return ToolResult(output=f"File not found: {file_path}", error=True)
        if not file_path.is_file():
            return ToolResult(output=f"Not a file: {file_path}", error=True)

        try:
            lines = file_path.read_text(errors="replace").splitlines(keepends=True)
            start = (offset or 1) - 1  # 1-indexed to 0-indexed
            end = start + limit if limit else len(lines)
            selected = lines[start:end]

            # Format with line numbers (cat -n style)
            numbered = []
            for i, line in enumerate(selected, start=start + 1):
                numbered.append(f"{i:6d}\t{line}")
            output = "".join(numbered)
            return ToolResult(output=output)
        except Exception as e:
            return ToolResult(output=str(e), error=True)
