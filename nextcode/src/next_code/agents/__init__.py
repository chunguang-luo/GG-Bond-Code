"""Agent 系统 — 多 Agent 协调框架。

公共 API：
- AgentDefinition: Agent 的数据蓝图
- AgentSource: Agent 来源枚举
- get_active_agents(): 获取当前所有活跃 Agent（合并+去重）
- get_builtin_agents(): 获取内置 Agent 列表
- run_agent(): 运行子 Agent 的完整生命周期
"""

from .definition import AgentDefinition, AgentSource
from .builtins import get_builtin_agents
from .loader import get_active_agents, load_custom_agents
from .runner import run_agent

__all__ = [
    "AgentDefinition",
    "AgentSource",
    "get_active_agents",
    "get_builtin_agents",
    "load_custom_agents",
    "run_agent",
]
