"""Tool base class and registry — mirrors Tool.ts + tools.ts."""

from __future__ import annotations

import asyncio
import contextvars
import json
import traceback
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

# Context variable for injecting ToolUseContext into tools during execution.
# Uses contextvars instead of instance attributes because ToolRegistry stores
# one instance per tool name — concurrent tool calls share the same instance,
# so instance-level _context would be unsafe. contextvars are inherently safe.
_current_context: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "_tool_context", default=None,
)


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

    @property
    def _context(self) -> Any | None:
        """Access the current ToolUseContext via contextvars.

        Set by QueryRunner before tool execution, cleared after.
        """
        return _current_context.get()

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

    def is_concurrency_safe(self, params: dict[str, Any]) -> bool:
        """Return True if this tool call is safe to run concurrently with others.

        Read-only tools (Read, Glob, Grep) return True.
        Write tools (Edit, Write, Bash) return False.
        Subclasses can override for param-dependent decisions.
        """
        return False

    def is_read_only(self, params: dict[str, Any]) -> bool:
        """Return True if this tool only reads without modifying anything.

        Defaults to False (fail-closed: assume write unless explicitly declared).
        Used by PermissionManager to auto-allow read-only tools.
        """
        return False

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
    """Global tool registry — mirrors getAllBaseTools() in tools.ts.

    Includes session-level schema cache to prevent tool definition byte
    jitter across requests. Prompt Cache requires byte-consistency — if
    a tool's schema changes between requests, the cached prefix breaks
    from the tools section onward, invalidating all message-level cache.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # Key: (tool_name, family) — different API families have different formats
        self._schema_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        # Invalidate cache for this tool on re-registration
        keys_to_remove = [k for k in self._schema_cache if k[0] == tool.name]
        for k in keys_to_remove:
            self._schema_cache.pop(k, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def to_api_format(self, family: str = "openai") -> list[dict[str, Any]]:
        """Convert all tools to API format with session-level schema caching.

        Each tool's schema is serialized only once per (name, family) combo.
        Subsequent calls return the cached dict, guaranteeing byte-consistency
        for Prompt Cache hit rate.
        """
        result = []
        for name, tool in self._tools.items():
            cache_key = (name, family)
            if cache_key not in self._schema_cache:
                self._schema_cache[cache_key] = tool.to_api_format(family)
            result.append(self._schema_cache[cache_key])
        return result

    def invalidate_schema_cache(self, tool_name: str | None = None) -> None:
        """Invalidate schema cache for a tool, or all tools if None."""
        if tool_name:
            keys_to_remove = [k for k in self._schema_cache if k[0] == tool_name]
            for k in keys_to_remove:
                self._schema_cache.pop(k, None)
        else:
            self._schema_cache.clear()


def create_default_registry() -> ToolRegistry:
    """Create registry with all default tools."""
    from .bash import BashTool
    from .file_read import FileReadTool
    from .file_edit import FileEditTool
    from .file_write import FileWriteTool
    from .glob import GlobTool
    from .grep import GrepTool
    from .skill import SkillTool

    registry = ToolRegistry()
    for tool_cls in [BashTool, FileReadTool, FileEditTool, FileWriteTool, GlobTool, GrepTool, SkillTool]:
        registry.register(tool_cls())
    return registry
