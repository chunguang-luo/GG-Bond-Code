"""MCP tool proxy — wraps remote MCP tools as built-in Tool interface.

Why a proxy:
LLM only knows the Tool interface. The proxy pattern lets MCP tools
integrate without modifying the LLM call logic.

Why truncate descriptions:
Some OpenAPI-generated MCP servers embed the entire API docs as tool
descriptions — single descriptions can reach 15-60KB. This bloats
token usage. 2048 chars preserves key info without waste.
"""
from __future__ import annotations

from typing import Any, Callable, Awaitable

from ..tools.base import Tool, ToolResult
from .naming import build_mcp_tool_name

# Description truncation threshold
MAX_MCP_DESCRIPTION_LENGTH = 2048


class MCPToolProxy(Tool):
    """Proxy that wraps a remote MCP tool as a built-in Tool.

    name follows mcp__<server>__<tool> convention.
    description is truncated to 2048 chars if too long.
    execute() delegates to MCPClient.call_tool().
    """

    name: str
    description: str

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        tool_info: dict[str, Any],
        get_client: Callable[[], Awaitable["MCPClient"]],  # noqa: F821
    ) -> None:
        self.server_name = server_name
        self.tool_name = tool_name
        self._tool_info = tool_info
        self._get_client = get_client
        self._input_schema = tool_info.get("inputSchema", {})

        # Fully-qualified name
        self.name = build_mcp_tool_name(server_name, tool_name)

        # Description (truncated if needed)
        desc = tool_info.get("description", "") or ""
        if len(desc) > MAX_MCP_DESCRIPTION_LENGTH:
            desc = desc[:MAX_MCP_DESCRIPTION_LENGTH] + "… [truncated]"
        self.description = desc

    def get_schema(self) -> dict[str, Any]:
        """Return the tool's parameter JSON Schema."""
        return self._input_schema

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Execute the MCP tool by delegating to the MCP client."""
        try:
            client = await self._get_client()
            content = await client.call_tool(self.tool_name, params)

            # Convert MCP content items to text output
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif item.get("type") == "image":
                        text_parts.append(f"[Image: {item.get('mimeType', 'unknown')}]")
                    elif item.get("type") == "resource":
                        # Embedded resource (e.g., file content)
                        resource = item.get("resource", {})
                        text_parts.append(resource.get("text", str(resource)))
                    else:
                        text_parts.append(str(item))
                else:
                    text_parts.append(str(item))

            return ToolResult(output="\n".join(text_parts))

        except Exception as e:
            return ToolResult(
                output=f"MCP tool error ({self.server_name}/{self.tool_name}): {e}",
                error=True,
            )

    def is_concurrency_safe(self, params: dict[str, Any]) -> bool:
        """Use MCP annotations to determine if concurrent calls are safe."""
        annotations = self._tool_info.get("annotations", {})
        return annotations.get("readOnlyHint", False)

    def is_read_only(self, params: dict[str, Any]) -> bool:
        """Use MCP annotations to determine if the tool is read-only."""
        annotations = self._tool_info.get("annotations", {})
        return annotations.get("readOnlyHint", False)

    def get_timeout(self) -> float:
        """MCP tools get a longer timeout — they may call external services."""
        return 300.0
