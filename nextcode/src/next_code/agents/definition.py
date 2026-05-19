"""AgentDefinition — Agent 的数据蓝图。

镜像 Claude Code 的 BaseAgentDefinition，承载 Agent 的所有配置维度。
整个 Agent 系统的基础——runAgent()、工具过滤、上下文隔离都依赖它。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class AgentSource(str, Enum):
    """Agent 来源类型。"""

    BUILTIN = "built-in"
    CUSTOM = "custom"  # 用户 .nextcode/agents/*.md
    PLUGIN = "plugin"


@dataclass
class AgentDefinition:
    """Agent 的数据蓝图。

    agent_type 是唯一标识，用于路由和去重。
    name 只是展示用，可以重复。
    去重时后面的 agent_type 覆盖前面的。
    """

    agent_type: str  # 唯一标识，用于路由和去重
    name: str  # 展示名称
    description: str = ""  # 何时使用此 Agent（展示给模型选择）
    source: AgentSource = AgentSource.BUILTIN

    # ── 工具控制 ──────────────────────────────────────────────
    tools: list[str] | None = None  # None 或 ['*'] 表示全部
    disallowed_tools: list[str] = field(default_factory=list)

    # ── 模型与推理 ──────────────────────────────────────────────
    model: str | None = None  # None = 继承父级
    max_turns: int | None = None  # 最大对话轮次

    # ── 上下文优化 ──────────────────────────────────────────────
    omit_nextcode_md: bool = False  # 省略 NEXTCODE.md（为只读 Agent 省 token）
    omit_git_status: bool = False  # 省略 gitStatus

    # ── 运行模式 ──────────────────────────────────────────────
    background: bool = False  # 是否作为后台任务运行
    memory_scope: str | None = None  # 'user' | 'project' | 'local'

    # ── 权限覆盖 ──────────────────────────────────────────────
    permission_mode: str | None = None  # 覆盖父级权限模式

    # ── System Prompt ──────────────────────────────────────────
    get_system_prompt: Callable[[], str] | None = None
