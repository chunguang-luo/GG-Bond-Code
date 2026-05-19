"""Markdown Agent 定义文件解析器。

格式：YAML frontmatter + Markdown body（作为 system prompt）

与 skills/frontmatter.py 的设计一致：
- 使用简单的行级 YAML 解析器，避免 pyyaml 依赖
- frontmatter 和 body 用 --- 分隔
- 解析失败返回 None 而非抛异常（容错性）

示例 .nextcode/agents/researcher.md：

    ---
    name: researcher
    description: "Deep codebase researcher"
    tools:
      - FileRead
      - Glob
      - Grep
    model: inherit
    max_turns: 30
    ---

    You are a deep research specialist...
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .definition import AgentDefinition, AgentSource

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n?---\s*\n", re.DOTALL)


def parse_agent_from_markdown(filepath: Path) -> AgentDefinition | None:
    """从 Markdown 文件解析 Agent 定义。

    Returns:
        AgentDefinition 或 None（文件格式无效时）
    """
    try:
        content = filepath.read_text(encoding="utf-8")
    except OSError:
        return None

    match = _FRONTMATTER_RE.match(content)
    if not match:
        return None

    yaml_str = match.group(1)
    body = content[match.end() :].strip()

    fm = _parse_yaml_lines(yaml_str)

    agent_type = fm.get("name") or filepath.stem
    if not agent_type:
        return None

    # 解析工具列表
    tools = fm.get("tools")
    disallowed = fm.get("disallowed_tools", [])
    if not isinstance(disallowed, list):
        disallowed = [str(disallowed)]

    # 解析布尔字段
    omit_claude_md = _parse_bool(fm.get("omit_claude_md", False))
    omit_git_status = _parse_bool(fm.get("omit_git_status", False))
    background = _parse_bool(fm.get("background", False))

    # 解析 system prompt — 使用默认参数实现早期绑定
    system_prompt = body if body else None
    get_system_prompt = (lambda prompt=system_prompt: prompt) if system_prompt else None

    return AgentDefinition(
        agent_type=agent_type,
        name=fm.get("name", agent_type),
        description=fm.get("description", ""),
        source=AgentSource.CUSTOM,
        tools=tools,
        disallowed_tools=disallowed,
        model=fm.get("model"),
        max_turns=fm.get("max_turns"),
        omit_claude_md=omit_claude_md,
        omit_git_status=omit_git_status,
        background=background,
        memory_scope=fm.get("memory"),
        permission_mode=fm.get("permission_mode"),
        get_system_prompt=get_system_prompt,
    )


# ── Internal helpers ──────────────────────────────────────────────────────


def _parse_yaml_lines(yaml_str: str) -> dict[str, Any]:
    """简单行级 YAML 解析器，支持 flat key-value + 列表。

    处理：
    - key: value（标量）
    - key: [a, b]（内联列表）
    - key:（空值 → 开始多行列表收集）
    - - item（多行列表项）
    - # 注释
    """
    result: dict[str, Any] = {}
    lines = yaml_str.splitlines()

    current_key: str | None = None
    current_list: list[str] | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # 多行列表项: "  - value"
        if line.startswith("- ") and current_key is not None and current_list is not None:
            item = line[2:].strip().strip('"').strip("'")
            current_list.append(item)
            continue

        if ":" not in line:
            continue

        # 刷出之前的多行列表
        if current_list is not None and current_key is not None:
            result[current_key] = current_list
            current_key = None
            current_list = None

        key, _, value = line.partition(":")
        key = key.strip().lower().replace("-", "_")
        value = value.strip()

        # 空值 → 开始多行列表收集
        if not value:
            current_key = key
            current_list = []
            continue

        # 内联列表: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",")]
            result[key] = [v for v in items if v]
            continue

        # 尝试解析为整数
        try:
            result[key] = int(value)
            continue
        except ValueError:
            pass

        # 标量值
        result[key] = value.strip('"').strip("'")

    # 刷出尾部多行列表
    if current_list is not None and current_key is not None:
        result[current_key] = current_list

    return result


def _parse_bool(value: Any) -> bool:
    """将各种格式的布尔值转为 Python bool。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1")
    return bool(value)
