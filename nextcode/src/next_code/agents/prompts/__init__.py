"""Agent System Prompt 模块。

构建函数 + 内置 Agent 的 prompt 模板。
"""

from __future__ import annotations

from ..definition import AgentDefinition
from ...state.context import ToolUseContext

from .explore import EXPLORE_SYSTEM_PROMPT
from .plan import PLAN_SYSTEM_PROMPT
from .general import GENERAL_SYSTEM_PROMPT

# 内置 Agent 类型的 prompt 映射
_BUILTIN_PROMPTS: dict[str, str] = {
    "Explore": EXPLORE_SYSTEM_PROMPT,
    "Plan": PLAN_SYSTEM_PROMPT,
    "general-purpose": GENERAL_SYSTEM_PROMPT,
}


def build_agent_system_prompt(
    agent_def: AgentDefinition,
    context: ToolUseContext,
) -> str:
    """构建子 Agent 的 System Prompt。

    组装逻辑：
    1. Agent 自身的 system prompt（来自 Markdown body 或内置定义）
    2. 只读模式约束（如果 Agent 禁止写工具）
    """
    parts: list[str] = []

    # Agent 自身的 System Prompt
    if agent_def.get_system_prompt:
        parts.append(agent_def.get_system_prompt())
    elif agent_def.agent_type in _BUILTIN_PROMPTS:
        parts.append(_BUILTIN_PROMPTS[agent_def.agent_type])

    # 只读模式约束
    if agent_def.disallowed_tools:
        write_tools = {"Edit", "Write", "NotebookEdit"}
        disallowed_set = set(agent_def.disallowed_tools)
        if write_tools & disallowed_set:
            parts.append(
                "=== CRITICAL: READ-ONLY MODE ===\n"
                "You CANNOT edit, write, or create files. "
                "You can only read and search.\n"
                "Report your findings concisely with file paths and line numbers."
            )

    return "\n\n".join(parts)


__all__ = [
    "EXPLORE_SYSTEM_PROMPT",
    "PLAN_SYSTEM_PROMPT",
    "GENERAL_SYSTEM_PROMPT",
    "build_agent_system_prompt",
]
