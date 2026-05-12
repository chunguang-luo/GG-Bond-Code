"""FileEditTool — exact string replacement in files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Tool, ToolResult


class FileEditTool(Tool):
    name = "Edit"
    description = "Perform exact string replacements in a file."

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to edit",
                },
                "old_string": {
                    "type": "string",
                    "description": "The text to replace",
                },
                "new_string": {
                    "type": "string",
                    "description": "The text to replace it with",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default false)",
                    "default": False,
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        file_path = Path(params["file_path"])
        old_string = params["old_string"]
        new_string = params["new_string"]
        replace_all = params.get("replace_all", False)

        if not file_path.exists():
            return ToolResult(output=f"File not found: {file_path}", error=True)

        # Safety check: block edits to partially-viewed files
        if self._context is not None:
            allowed, reason = self._context.file_cache.can_edit(str(file_path))
            if not allowed:
                return ToolResult(output=f"Edit blocked: {reason}", error=True)

        content = file_path.read_text()

        if old_string not in content:
            return ToolResult(output="old_string not found in file", error=True)

        if not replace_all and content.count(old_string) > 1:
            return ToolResult(
                output=f"old_string appears {content.count(old_string)} times — use replace_all or provide more context",
                error=True,
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        file_path.write_text(new_content)

        # Update file cache after successful edit
        if self._context is not None:
            try:
                self._context.file_cache.record_edit(str(file_path), new_content)
            except Exception:
                pass  # Cache update is best-effort

        return ToolResult(output=f"Edited {file_path}")
