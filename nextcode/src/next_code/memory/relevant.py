"""Relevant Memories — 预取 + 去重 + 注入。

协调 Scan → Select → Load → Inject 流程，
将相关的记忆文件内容注入到对话上下文中。
"""

from __future__ import annotations

import logging
import os

from .paths import get_auto_mem_path
from .scan import MemoryHeader, scan_memory_files
from .select import select_relevant_memories

logger = logging.getLogger(__name__)

MAX_RELEVANT_MEMORY_TOKENS = 5000  # 相关记忆总 token 预算


def load_relevant_memories(
    query: str,
    cwd: str | None = None,
    surfaced_paths: set[str] | None = None,
) -> str | None:
    """加载与查询相关的记忆内容。

    三步流程：
    1. Scan: 扫描记忆目录中的文件头
    2. Select: 选择与查询相关的文件
    3. Load: 读取选中文件的完整内容

    Args:
        query: 用户查询
        cwd: 当前工作目录
        surfaced_paths: 已注入过的记忆路径（去重用）

    Returns:
        相关记忆内容（格式化的 Markdown），或 None（无相关记忆时）
    """
    memory_dir = get_auto_mem_path(cwd)

    # Step 1: Scan
    headers = scan_memory_files(memory_dir)
    if not headers:
        return None

    # Step 2: Select
    selected_paths = select_relevant_memories(query, headers, surfaced_paths)
    if not selected_paths:
        return None

    # Step 3: Load
    parts = []
    total_chars = 0
    max_chars = MAX_RELEVANT_MEMORY_TOKENS * 4  # 粗略估计 1 token ≈ 4 chars

    for filepath in selected_paths:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()

            if not content:
                continue

            filename = os.path.basename(filepath)
            part = f"### {filename}\n{content}"

            # 检查 token 预算
            if total_chars + len(part) > max_chars:
                # 截断这个文件
                remaining = max_chars - total_chars
                if remaining > 200:  # 至少保留 200 字符
                    content = content[: remaining - 20] + "\n... (truncated)"
                    part = f"### {filename}\n{content}"
                    parts.append(part)
                break

            parts.append(part)
            total_chars += len(part)

        except (IOError, OSError):
            continue

    if not parts:
        return None

    header = "[Relevant memories — loaded based on your query]"
    return f"{header}\n\n" + "\n\n".join(parts)


def get_all_surfed_paths(
    query: str,
    cwd: str | None = None,
) -> set[str]:
    """获取与查询相关的记忆文件路径（用于去重）。

    在注入前调用，获取将要被注入的文件路径集合，
    传给后续的 surfaced_paths 参数避免重复注入。
    """
    memory_dir = get_auto_mem_path(cwd)
    headers = scan_memory_files(memory_dir)
    if not headers:
        return set()

    selected_paths = select_relevant_memories(query, headers)
    return set(selected_paths)
