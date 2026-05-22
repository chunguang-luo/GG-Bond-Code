"""内置 Agent 注册。

用函数而非模块级常量，因为未来可能需要运行时条件决定是否包含某些 Agent。
"""

from __future__ import annotations

from .definition import AgentDefinition, AgentSource


def get_builtin_agents() -> list[AgentDefinition]:
    """返回内置 Agent 列表。"""
    return [
        AgentDefinition(
            agent_type="general-purpose",
            name="general-purpose",
            description="General-purpose agent for complex, multi-step tasks. "
            "Use when you need to search for a keyword or file, or when you are "
            "not confident that a single, focused sub-agent can handle the task.",
            source=AgentSource.BUILTIN,
            tools=["*"],
            memory_scope="local",  # 本地记忆，随项目但不提交 Git
        ),
        AgentDefinition(
            agent_type="Explore",
            name="Explore",
            description="Fast agent specialized for exploring codebases. "
            "Use when you need to quickly find files by patterns, search code "
            "for keywords, or answer questions about the codebase. Thoroughness "
            "level: quick for basic searches, medium for moderate exploration, "
            "very thorough for comprehensive analysis.",
            source=AgentSource.BUILTIN,
            disallowed_tools=["Edit", "NotebookEdit", "Agent"],
            model=None,  # inherit from parent
            omit_nextcode_md=True,
            omit_git_status=True,
            memory_scope="project",  # 项目级记忆，团队共享项目结构知识
        ),
        AgentDefinition(
            agent_type="Plan",
            name="Plan",
            description="Software architect agent for designing implementation plans. "
            "Use when you need to plan the implementation strategy for a task. "
            "Returns step-by-step plans, identifies critical files, and considers "
            "architectural trade-offs.",
            source=AgentSource.BUILTIN,
            disallowed_tools=["Edit", "NotebookEdit"],
            omit_nextcode_md=True,
            omit_git_status=True,
            memory_scope="local",  # 本地记忆，记住用户偏好（如先列选项再实现）
        ),
        AgentDefinition(
            agent_type="Verification",
            name="Verification",
            description="Adversarial code review agent that finds problems, "
            "not confirms correctness. Use for verifying changes, reviewing PRs, "
            "or auditing code. Actually runs commands and tests rather than just "
            "reading code. Returns PASS/FAIL/PARTIAL verdict.",
            source=AgentSource.BUILTIN,
            disallowed_tools=["Edit", "NotebookEdit"],
            model=None,  # inherit — needs strong reasoning
            permission_mode="dontAsk",
            omit_nextcode_md=True,
            omit_git_status=True,
            memory_scope="local",  # 本地记忆，记住本机测试配置
        ),
        AgentDefinition(
            agent_type="Guide",
            name="Guide",
            description="Help users understand NextCode features and capabilities. "
            "Use when users ask 'how do I...', 'can NextCode...', or need help "
            "navigating commands, skills, and configuration.",
            source=AgentSource.BUILTIN,
            tools=["Glob", "Grep", "Read", "WebFetch", "WebSearch"],
            model=None,  # inherit
            permission_mode="dontAsk",
            omit_nextcode_md=True,
            omit_git_status=True,
            memory_scope="user",  # 用户级记忆，跨项目共享功能知识
            get_system_prompt=_build_guide_prompt,
        ),
    ]


def _build_guide_prompt() -> str:
    """Build Guide Agent's dynamic system prompt."""
    from .prompts.guide import build_guide_system_prompt
    return build_guide_system_prompt(context=None)
