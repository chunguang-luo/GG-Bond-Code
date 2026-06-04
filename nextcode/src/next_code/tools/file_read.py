"""FileReadTool — read file contents with cache support."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Tool, ToolResult


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

    def is_concurrency_safe(self, params: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        file_path_str = params.get("file_path")
        if not file_path_str:
            return ToolResult(
                output="Read 工具缺少 file_path 参数，请确保提供了要读取的文件绝对路径",
                error=True,
            )
        file_path = Path(file_path_str)
        offset = params.get("offset")
        limit = params.get("limit")

        if not file_path.exists():
            return ToolResult(output=f"File not found: {file_path}", error=True)
        if not file_path.is_file():
            return ToolResult(output=f"Not a file: {file_path}", error=True)

        try:
            # Get file mtime for cache validation
            try:
                stat_result = file_path.stat()
                mtime = stat_result.st_mtime
            except OSError:
                mtime = 0.0

            # Check cache first — skip disk I/O if file unchanged
            full_text = None
            if self._context is not None:
                try:
                    full_text = self._context.file_cache.get_cached_content(
                        str(file_path), mtime
                    )
                except Exception:
                    pass  # Cache lookup is best-effort

            # Cache miss — read from disk
            if full_text is None:
                full_text = file_path.read_text(errors="replace")

            lines = full_text.splitlines(keepends=True)
            start = (offset or 1) - 1  # 1-indexed to 0-indexed
            end = start + limit if limit else len(lines)
            selected = lines[start:end]

            # Format with line numbers (cat -n style)
            numbered = []
            for i, line in enumerate(selected, start=start + 1):
                numbered.append(f"{i:6d}\t{line}")
            output = "".join(numbered)

            # Record state in FileStateCache (best-effort)
            if self._context is not None:
                try:
                    self._context.file_cache.record_read(
                        path=str(file_path),
                        content=full_text,
                        offset=offset,
                        limit=limit,
                        mtime=mtime,
                    )
                except Exception:
                    pass  # Cache recording is best-effort

            return ToolResult(output=output)
        except Exception as e:
            return ToolResult(output=str(e), error=True)
