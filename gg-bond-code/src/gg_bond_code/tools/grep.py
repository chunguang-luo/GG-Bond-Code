"""GrepTool — content search using regex."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult


class GrepTool(Tool):
    name = "Grep"
    description = "Search file contents with a regex pattern."

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in",
                },
                "glob": {
                    "type": "string",
                    "description": "File pattern to filter (e.g. '*.py')",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case insensitive search (default false)",
                    "default": False,
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        pattern = params["pattern"]
        search_path = Path(params.get("path", "."))
        glob_filter = params.get("glob")
        case_insensitive = params.get("case_insensitive", False)

        flags = re.IGNORECASE if case_insensitive else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(output=f"Invalid regex: {e}", error=True)

        try:
            results: list[str] = []
            files = search_path.glob(glob_filter) if glob_filter else _iter_all_files(search_path)

            for file_path in files:
                if not file_path.is_file():
                    continue
                try:
                    for i, line in enumerate(file_path.read_text(errors="replace").splitlines(), 1):
                        if regex.search(line):
                            results.append(f"{file_path}:{i}: {line.strip()}")
                            if len(results) >= 250:
                                break
                except (OSError, UnicodeDecodeError):
                    continue
                if len(results) >= 250:
                    break

            output = "\n".join(results) if results else "No matches found"
            return ToolResult(output=output)
        except Exception as e:
            return ToolResult(output=str(e), error=True)


def _iter_all_files(path: Path):
    """Iterate all files recursively."""
    if path.is_file():
        yield path
    else:
        yield from path.rglob("*")
