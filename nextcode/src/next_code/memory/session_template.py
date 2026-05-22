"""Session Memory 模板 — 10 段 Markdown。

每个 section 有固定标题和斜体描述行，AI 只能修改描述行之后的内容。
"""

from __future__ import annotations

DEFAULT_SESSION_MEMORY_TEMPLATE = """# Session Title
_A short and distinctive 5-10 word descriptive title..._

# Current State
_What is actively being worked on right now?..._

# Task specification
_What did the user ask to build?..._

# Files and Functions
_What are the important files?..._

# Workflow
_What bash commands are usually run?..._

# Errors & Corrections
_Errors encountered and how they were fixed..._

# Codebase and System Documentation
_What are the important system components?..._

# Learnings
_What has worked well? What has not?..._

# Key results
_If the user asked a specific output..._

# Worklog
_Step by step, what was attempted, done?..._
"""

MAX_SECTION_LENGTH = 2000  # 每段 2000 token 软限制
MAX_TOTAL_SESSION_MEMORY_TOKENS = 12000  # 总文件 12000 token 硬限制


def create_empty_session_memory() -> str:
    """创建空的 Session Memory 模板。"""
    return DEFAULT_SESSION_MEMORY_TEMPLATE


def parse_sections(content: str) -> dict[str, str]:
    """解析 Session Memory 的各个段。

    Returns:
        段标题到段内容的映射。
    """
    sections: dict[str, str] = {}
    current_title = ""
    current_lines: list[str] = []

    for line in content.split("\n"):
        if line.startswith("# ") and not line.startswith("# "):
            continue
        if line.startswith("# "):
            # 保存上一个段
            if current_title:
                sections[current_title] = "\n".join(current_lines).strip()
            current_title = line[2:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    # 保存最后一个段
    if current_title:
        sections[current_title] = "\n".join(current_lines).strip()

    return sections


def is_template_default(section_content: str) -> bool:
    """判断一段内容是否还是模板默认值（斜体描述行）。"""
    stripped = section_content.strip()
    if not stripped:
        return True
    # 模板默认值以斜体开头和结尾
    if stripped.startswith("_") and stripped.endswith("..._"):
        return True
    return False
