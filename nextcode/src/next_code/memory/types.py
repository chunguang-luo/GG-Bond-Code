"""记忆分类法 — 四种闭合类型。

每种类型有明确的写入条件和排除规则。
"What NOT to save" 同等重要——可从当前状态派生的信息不需要记忆。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MemoryType(str, Enum):
    """四种闭合记忆类型。"""

    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


MEMORY_TYPES = [t.value for t in MemoryType]

# 类型元信息：写入时机、不应保存的内容
TYPE_META = {
    MemoryType.USER: {
        "meaning": "用户角色、偏好、知识水平",
        "write_when": "了解到用户信息时",
        "dont_save": "负面评价",
    },
    MemoryType.FEEDBACK: {
        "meaning": "行为纠正 + 正向确认",
        "write_when": "用户纠正或确认做法时",
        "dont_save": "仅保存纠正而忽视确认",
    },
    MemoryType.PROJECT: {
        "meaning": "项目背景、决策、截止日期",
        "write_when": "了解到不可从代码推导的项目信息时",
        "dont_save": "可从 git log 推导的内容",
    },
    MemoryType.REFERENCE: {
        "meaning": "外部系统指针",
        "write_when": "了解到外部资源位置时",
        "dont_save": "系统的具体内容（只存指针）",
    },
}

# 不应保存的 5 类内容
WHAT_NOT_TO_SAVE = [
    "代码模式、约定、架构——可从当前项目状态派生",
    "Git 历史、最近变更——git log 是权威来源",
    "调试方案或修复方法——修复在代码中，commit message 有上下文",
    "CLAUDE.md / NEXTCODE.md 已有的内容",
    "临时任务状态——进行中的工作、对话上下文",
]


@dataclass
class MemoryFile:
    """一个记忆文件的元信息。"""

    filename: str
    name: str
    description: str
    type: MemoryType
    path: str


def parse_frontmatter(content: str, filepath: str = "") -> dict[str, Any]:
    """解析 Markdown frontmatter。

    期望格式：
    ---
    name: {{memory name}}
    description: {{one-line description}}
    type: {{user|feedback|project|reference}}
    ---

    {{memory content}}
    """
    meta: dict[str, Any] = {"name": "", "description": "", "type": None, "content": content}

    if not content.startswith("---"):
        return meta

    end = content.find("---", 3)
    if end == -1:
        return meta

    fm_text = content[3:end].strip()
    body = content[end + 3:].strip()

    for line in fm_text.split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        if key == "name":
            meta["name"] = value
        elif key == "description":
            meta["description"] = value
        elif key == "type":
            try:
                meta["type"] = MemoryType(value)
            except ValueError:
                meta["type"] = None

    meta["content"] = body
    return meta


def build_frontmatter(name: str, description: str, mem_type: MemoryType) -> str:
    """构建 frontmatter 头。"""
    return f"---\nname: {name}\ndescription: {description}\ntype: {mem_type.value}\n---\n\n"