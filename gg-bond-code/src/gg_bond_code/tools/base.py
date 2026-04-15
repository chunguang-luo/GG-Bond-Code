"""Tool base class and registry — mirrors Tool.ts + tools.ts."""

from __future__ import annotations

import asyncio
import json
import traceback
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    """Result from tool execution."""
    output: str
    error: bool = False
    metadata: dict[str, Any] = {}


class Tool(ABC):
    """Base class for all tools — mirrors the Tool interface in Tool.ts."""

    name: str
    description: str

    def __init__(self) -> None:
        self.name = self.__class__.name
        self.description = self.__class__.description

    @abstractmethod
    def get_schema(self) -> dict[str, Any]:
        """Return JSON Schema for tool parameters (used in API call)."""

    @abstractmethod
    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Execute the tool with given parameters."""

    async def execute_safe(self, params: dict[str, Any]) -> ToolResult:
        """Execute with timeout protection and exception catching."""
        try:
            return await asyncio.wait_for(
                self.execute(params),
                timeout=self.get_timeout(),
            )
        except asyncio.TimeoutError:
            return ToolResult(output=f"Tool execution timed out ({self.get_timeout():.0f}s)", error=True)
        except Exception as e:
            tb = traceback.format_exc()
            return ToolResult(output=f"Tool error: {e}\n{tb}", error=True)

    def get_timeout(self) -> float:
        """Return timeout in seconds for execute_safe."""
        return 120.0

    def to_api_format(self, family: str = "openai") -> dict[str, Any]:
        """Convert to API tool format. family='anthropic' or 'openai'."""
        if family == "anthropic":
            return {
                "name": self.name,
                "description": self.description,
                "input_schema": self.get_schema(),
            }
        # OpenAI function-calling format
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.get_schema(),
            },
        }


class ToolRegistry:
    """Global tool registry — mirrors getAllBaseTools() in tools.ts."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def to_api_format(self, family: str = "openai") -> list[dict[str, Any]]:
        return [tool.to_api_format(family) for tool in self._tools.values()]


def create_default_registry() -> ToolRegistry:
    """Create registry with all default tools."""
    from .bash import BashTool
    from .file_read import FileReadTool
    from .file_edit import FileEditTool
    from .file_write import FileWriteTool
    from .glob import GlobTool
    from .grep import GrepTool

    registry = ToolRegistry()
    for tool_cls in [BashTool, FileReadTool, FileEditTool, FileWriteTool, GlobTool, GrepTool]:
        registry.register(tool_cls())
    return registry
