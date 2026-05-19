"""Agent 工具过滤 — 三层机制，总体收敛但有硬编码例外通道。

三层：
1. 全局禁止：不管什么 Agent，某些工具就是不能用
2. 异步限制：后台 Agent 只能用安全的工具
3. Agent 定义级：每个 Agent 可以进一步收缩自己的工具集

例外通道：
- MCP 工具（mcp__ 前缀）穿透所有层级
- 这些例外都是硬编码的白名单，不能被运行时输入扩展
"""

from __future__ import annotations

from ..agents.definition import AgentDefinition
from .base import Tool


# ── 第一层：全局禁止 ──────────────────────────────────────────

ALL_AGENT_DISALLOWED_TOOLS: set[str] = {
    "AskUserQuestion",  # 子 Agent 不应直接问用户问题
    "ExitPlanMode",     # 子 Agent 不应控制 plan 状态
    "EnterPlanMode",    # 同上
    "TaskStop",         # 子 Agent 不应停止其他任务
    "TaskOutput",       # 子 Agent 不应读取其他任务输出
}


# ── 第二层：异步 Agent 限制 ──────────────────────────────────────

ASYNC_AGENT_ALLOWED_TOOLS: set[str] = {
    "Read", "Glob", "Grep",              # 只读搜索
    "WebSearch", "WebFetch",               # 外部信息获取
    "TodoWrite",                            # 任务追踪
    "Edit", "Write", "NotebookEdit",       # 文件操作
    "Bash",                                 # 命令执行
    "Skill",                                # 技能调用
}


# ── 第三层 + 主入口 ──────────────────────────────────────────────


def resolve_agent_tools(
    agent_def: AgentDefinition,
    available_tools: list[Tool],
    *,
    is_async: bool = False,
) -> list[Tool]:
    """三层工具过滤的主入口。

    返回 list[Tool]（保留原始顺序，影响 System Prompt 中工具描述的顺序）。

    Args:
        agent_def: Agent 定义（包含 tools/disallowed_tools）
        available_tools: 当前可用的所有工具
        is_async: 是否异步运行的后台 Agent
    """
    # 第一层：全局禁止
    filtered = [
        t for t in available_tools
        if _is_tool_allowed_globally(t)
    ]

    # 第二层：异步限制
    if is_async:
        async_allowed = ASYNC_AGENT_ALLOWED_TOOLS
        filtered = [
            t for t in filtered
            if t.name in async_allowed or t.name.startswith("mcp__")
        ]

    # 第三层：Agent 定义级
    disallowed = set(agent_def.disallowed_tools)
    filtered = [t for t in filtered if t.name not in disallowed]

    # 应用 tools 白名单（如果定义了且不是通配符）
    if agent_def.tools is not None and agent_def.tools != ["*"]:
        tool_set = set(agent_def.tools)
        filtered = [t for t in filtered if t.name in tool_set]

    return filtered


def _is_tool_allowed_globally(tool: Tool) -> bool:
    """检查工具是否通过全局禁止检查。

    例外：mcp__ 前缀的工具无条件放行（信任决策在定义时已做出）。
    """
    if tool.name.startswith("mcp__"):
        return True

    return tool.name not in ALL_AGENT_DISALLOWED_TOOLS
