"""Tool base class and registry — mirrors Tool.ts + tools.ts."""

from __future__ import annotations

import json
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
    from gg_bond_code.tools.bash import BashTool
    from gg_bond_code.tools.file_read import FileReadTool
    from gg_bond_code.tools.file_edit import FileEditTool
    from gg_bond_code.tools.file_write import FileWriteTool
    from gg_bond_code.tools.glob import GlobTool
    from gg_bond_code.tools.grep import GrepTool

    registry = ToolRegistry()
    for tool_cls in [BashTool, FileReadTool, FileEditTool, FileWriteTool, GlobTool, GrepTool]:
        registry.register(tool_cls())
    return registry
