"""Agent 多源加载与去重。

合并 built-in / plugin / custom 三个来源的 Agent 定义，
按优先级去重：同名 agent_type 后者覆盖前者。

优先级：built-in（低）< plugin（中）< custom（高）
"""

from __future__ import annotations

import logging
from pathlib import Path

from .definition import AgentDefinition
from .builtins import get_builtin_agents
from .markdown_parser import parse_agent_from_markdown

logger = logging.getLogger(__name__)


def _agents_dir(cwd: str) -> Path:
    """返回 .nextcode/agents/ 目录路径。"""
    return Path(cwd) / ".nextcode" / "agents"


def load_custom_agents(cwd: str) -> list[AgentDefinition]:
    """从 .nextcode/agents/*.md 加载用户自定义 Agent。

    单个文件解析失败不影响其他 Agent（容错性）。
    """
    agents_dir = _agents_dir(cwd)
    if not agents_dir.is_dir():
        return []

    agents: list[AgentDefinition] = []
    for md_file in sorted(agents_dir.glob("*.md")):
        try:
            agent = parse_agent_from_markdown(md_file)
            if agent is not None:
                agents.append(agent)
        except Exception as e:
            logger.warning("Failed to load agent from %s: %s", md_file, e)
    return agents


def get_active_agents(cwd: str) -> list[AgentDefinition]:
    """合并所有来源并按优先级去重。

    去重策略：按 [built-in, plugin, custom] 顺序遍历，
    同名 agent_type 后者覆盖前者（自定义优先）。
    """
    all_agents: list[AgentDefinition] = []

    # 1. 内置 Agent（最低优先级）
    all_agents.extend(get_builtin_agents())

    # 2. 插件 Agent（中优先级）— 未来扩展
    # all_agents.extend(load_plugin_agents())

    # 3. 用户自定义 Agent（最高优先级）
    all_agents.extend(load_custom_agents(cwd))

    # 去重：同名 agent_type，后者覆盖前者
    agent_map: dict[str, AgentDefinition] = {}
    for agent in all_agents:
        agent_map[agent.agent_type] = agent

    return list(agent_map.values())
