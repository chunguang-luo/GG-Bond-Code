"""GlobTool — file pattern matching."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Tool, ToolResult


class GlobTool(Tool):
    name = "Glob"
    description = "Find files matching a glob pattern."

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match files against (e.g. '**/*.py')",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: cwd)",
                },
            },
            "required": ["pattern"],
        }

    def is_concurrency_safe(self, params: dict[str, Any]) -> bool:
        return True

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        pattern = params["pattern"]
        search_path = Path(params.get("path", "."))

        if not search_path.exists():
            return ToolResult(output=f"Directory not found: {search_path}", error=True)

        try:
            matches = sorted(search_path.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            output = "\n".join(str(m) for m in matches[:250])
            if not output:
                output = "No files matched the pattern"
            return ToolResult(output=output)
        except Exception as e:
            return ToolResult(output=str(e), error=True)
