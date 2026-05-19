"""AgentTool — 让模型可以调用子 Agent。

模型通过调用 AgentTool 来委派任务给子 Agent，参数中指定 Agent 类型
和指令。AgentTool 是 Agent 系统的"用户界面"——模型只需知道"有一个叫
Agent 的工具可以用"。

示例：模型生成的工具调用
    {
        "name": "Agent",
        "arguments": {
            "prompt": "搜索项目中所有 API 端点定义",
            "subagent_type": "Explore"
        }
    }
"""

from __future__ import annotations

import logging
from typing import Any

from .base import Tool, ToolResult
from ..agents.definition import AgentDefinition
from ..agents.runner import run_agent
from ..agents.loader import get_active_agents

logger = logging.getLogger(__name__)


class AgentTool(Tool):
    name = "Agent"
    description = (
        "启动子 Agent 处理复杂或多步骤任务。"
        "需要搜索关键词或文件时，或不确定单一子 Agent 能否完成任务时，"
        "使用 general-purpose 类型。"
        "Explore 用于快速代码库搜索，Plan 用于实现方案规划。"
    )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "子 Agent 要执行的任务描述",
                },
                "subagent_type": {
                    "type": "string",
                    "description": (
                        "子 Agent 类型。"
                        "'Explore' 用于快速代码库搜索，"
                        "'Plan' 用于实现方案规划，"
                        "'general-purpose' 用于复杂多步骤任务。"
                    ),
                    "default": "general-purpose",
                },
                "description": {
                    "type": "string",
                    "description": "简短描述子 Agent 将要执行的任务（3-5个词）",
                    "default": "",
                },
            },
            "required": ["prompt"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        ctx = self._context
        if ctx is None:
            return ToolResult(output="Agent 工具：无法获取上下文", error=True)

        prompt = params.get("prompt", "")
        subagent_type = params.get("subagent_type", "general-purpose")
        is_async = params.get("run_in_background", False)

        # 检查嵌套深度 — 最多允许 2 层嵌套
        # depth=0 主Agent，depth=1 第一层子Agent，depth=2 第二层子Agent
        # depth>=3 时拒绝，即最多2层嵌套
        current_depth = getattr(ctx, "agent_depth", 0)
        if current_depth >= 3:
            return ToolResult(
                output=f"Agent 嵌套深度已达上限（当前 {current_depth} 层，最多 2 层），"
                       f"请直接使用工具完成任务，不要再启动子 Agent。",
                error=True,
            )

        # 1. 查找 Agent 定义
        cwd = ctx.get_state("cwd") or "."
        agents = get_active_agents(cwd)
        agent_def = _find_agent(agents, subagent_type)
        if agent_def is None:
            available = ", ".join(a.agent_type for a in agents)
            return ToolResult(
                output=f"未知的 Agent 类型: {subagent_type}，可用类型: {available}",
                error=True,
            )

        # 2. 运行 Agent，实时转发事件 + 收集结果
        # Use emit_ipc for real-time streaming directly to IPC transport,
        # bypassing the parent QueryRunner's yield loop which only drains
        # after the tool completes. This lets the user see sub-agent
        # progress (text output, tool calls) as they happen.
        emit_ipc = getattr(ctx, "emit_ipc", None)
        logger.info("AgentTool: emit_ipc=%s, subagent_type=%s", "available" if emit_ipc else "None", subagent_type)
        text_parts: list[str] = []
        try:
            async for event in run_agent(
                agent_def=agent_def,
                prompt=prompt,
                parent_context=ctx,
                is_async=is_async,
            ):
                # Attach the user prompt to agent_start for frontend display
                if event.type == "agent_start":
                    event.metadata["prompt"] = prompt
                # Stream events directly to IPC for real-time display
                if emit_ipc is not None and event.type in (
                    "agent_start", "agent_result", "text",
                    "tool_use", "tool_result", "error",
                ):
                    logger.debug("AgentTool: forwarding event type=%s source=%s", event.type, event.source)
                    await emit_ipc(event)
                # Collect assistant text for the final result
                if event.type == "text":
                    text_parts.append(event.content)
                elif event.type == "error":
                    return ToolResult(output=event.content, error=True)
        except Exception as e:
            return ToolResult(output=f"Agent 执行失败: {e}", error=True)

        # 3. 提取最终结果 — 返回完整输出给主流程 Agent 使用
        # 前端已通过 emit_ipc 实时显示了子 Agent 的输出
        result = "".join(text_parts).strip()
        if not result:
            result = "子 Agent 执行完毕，无输出"

        return ToolResult(output=result)

    def is_concurrency_safe(self, params: dict[str, Any]) -> bool:
        """Allow multiple Agent calls to run concurrently.

        Multiple sub-agents can safely run in parallel since each gets its
        own isolated context (create_subagent_context). The nesting depth
        limit (2 levels) is enforced inside execute() via agent_depth.
        """
        return True

    def get_timeout(self) -> float:
        """Agent tool needs more time — sub-agents may run multiple tool calls."""
        return 600.0  # 10 minutes

    def is_read_only(self, params: dict[str, Any]) -> bool:
        """AgentTool is read-only from the tool perspective.

        The sub-agent it spawns may modify files, but the AgentTool itself
        only orchestrates — it doesn't directly edit anything.
        """
        return True


def _find_agent(agents: list[AgentDefinition], agent_type: str) -> AgentDefinition | None:
    """按 agent_type 查找 Agent 定义。"""
    for agent in agents:
        if agent.agent_type == agent_type:
            return agent
    return None
