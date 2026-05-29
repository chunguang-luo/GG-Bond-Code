"""Agent Memory — 每个 Agent 的专属记忆空间。

三种 scope:
- user:   ~/.nextcode/agent-memory/<type>/  — 跨项目通用知识
- project: <cwd>/.nextcode/agent-memory/<type>/ — 项目特定，团队共享
- local:   <cwd>/.nextcode/agent-memory-local/<type>/ — 本地特定，不分享
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .types import MemoryType

# 匹配代码库内路径：src/xxx、frontend/xxx、tests/xxx 等
_PATH_PATTERN = re.compile(r"(?:^|[\s`(\[\"'])((?:src|frontend|tests|test|lib|pkg|cmd|internal|app|scripts|docs|config|tools)/[^\s`)\]\"',;]+)")


def get_agent_memory_dir(
    scope: str,
    agent_type: str,
    cwd: str | None = None,
) -> str:
    """获取 Agent Memory 目录路径。

    Args:
        scope: 'user' | 'project' | 'local'
        agent_type: Agent 类型标识
        cwd: 当前工作目录

    Returns:
        Agent Memory 目录的绝对路径

    Raises:
        ValueError: scope 不是有效值时
    """
    base = cwd or os.getcwd()

    if scope == "user":
        return str(Path.home() / ".nextcode" / "agent-memory" / agent_type)
    elif scope == "project":
        return str(Path(base) / ".nextcode" / "agent-memory" / agent_type)
    elif scope == "local":
        return str(Path(base) / ".nextcode" / "agent-memory-local" / agent_type)
    else:
        raise ValueError(f"Invalid agent memory scope: {scope}")


def load_agent_memory_prompt(
    scope: str,
    agent_type: str,
    cwd: str | None = None,
) -> str | None:
    """加载 Agent Memory Prompt 段。

    读取 Agent 记忆目录下的所有 .md 文件，组装为 Prompt 段。

    Args:
        scope: 'user' | 'project' | 'local'
        agent_type: Agent 类型标识
        cwd: 当前工作目录

    Returns:
        Agent Memory Prompt 文本，或 None（无记忆文件时）。
    """
    mem_dir = get_agent_memory_dir(scope, agent_type, cwd)

    # Fire-and-forget 目录创建
    # Agent 从 spawn 到实际写文件至少经过一个 API 往返（几百毫秒到几秒），
    # 而 mkdir 只需要微秒级别。
    os.makedirs(mem_dir, exist_ok=True)

    # 读取所有 .md 文件
    files = []
    if os.path.isdir(mem_dir):
        for entry in sorted(os.listdir(mem_dir)):
            if entry.endswith(".md"):
                filepath = os.path.join(mem_dir, entry)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        files.append((entry, f.read()))
                except (IOError, OSError):
                    continue

    if not files:
        return None

    # 过滤掉引用了不存在路径的记忆文件
    project_root = cwd or os.getcwd()
    valid_files = []
    for filename, content in files:
        paths = _PATH_PATTERN.findall(content)
        # 只验证 project/local scope 的路径（user scope 不绑定项目目录）
        if scope == "user" or not paths:
            valid_files.append((filename, content))
            continue
        if all(os.path.exists(os.path.join(project_root, p)) for p in paths):
            valid_files.append((filename, content))

    if not valid_files:
        return None

    # scope 指引
    scope_notes = {
        "user": "- Since this memory is user-scope, keep learnings general since they apply across all projects",
        "project": "- Since this memory is project-scope and shared via version control, tailor your memories to this project",
        "local": "- Since this memory is local-scope (not checked into version control), tailor to this project and machine",
    }

    sections = [f"## Agent Memory ({scope} scope)"]
    sections.append(scope_notes.get(scope, ""))
    sections.append(f"Memory directory: `{mem_dir}/`")
    sections.append("This directory already exists — write to it directly with the Write tool.")
    sections.append("")

    for filename, content in valid_files:
        sections.append(f"### {filename}\n{content}")

    return "\n".join(sections)
