"""记忆选择 — 双阶段召回的 Select 阶段。

用轻量 side query 选择最多 5 个相关记忆文件。
当前实现为基于关键词的简单匹配，未来可替换为轻量模型调用。
"""

from __future__ import annotations

import logging
import re

from .scan import MemoryHeader
from .types import MemoryType

logger = logging.getLogger(__name__)

MAX_SELECTED_MEMORIES = 5


def select_relevant_memories(
    query: str,
    memories: list[MemoryHeader],
    surfaced_paths: set[str] | None = None,
) -> list[str]:
    """选择与查询相关的记忆文件。

    当前实现为基于关键词的简单匹配。
    未来可替换为轻量 API 调用（如 deepseek-chat）。

    Args:
        query: 用户查询
        memories: 扫描到的记忆头列表
        surfaced_paths: 已注入过的记忆路径（去重用）

    Returns:
        选中的文件路径列表（最多 MAX_SELECTED_MEMORIES 个）
    """
    if not memories:
        return []

    surfaced = surfaced_paths or set()

    # 提取查询中的关键词
    query_lower = query.lower()
    query_words = set(re.findall(r"\w+", query_lower))

    # 为每个记忆计算相关性分数
    scored = []
    for mem in memories:
        # 跳过已注入的
        if mem.filepath in surfaced:
            continue

        score = _compute_relevance(query_words, query_lower, mem)
        if score > 0:
            scored.append((score, mem))

    if not scored:
        return []

    # 按分数降序排序，取前 MAX_SELECTED_MEMORIES 个
    scored.sort(key=lambda x: x[0], reverse=True)

    return [mem.filepath for _, mem in scored[:MAX_SELECTED_MEMORIES]]


def _compute_relevance(
    query_words: set[str],
    query_lower: str,
    mem: MemoryHeader,
) -> float:
    """计算记忆与查询的相关性分数。

    基于：
    1. 描述中的关键词匹配
    2. 文件名中的关键词匹配
    3. 类型权重（feedback 和 project 优先）
    4. 新鲜度权重
    """
    score = 0.0

    # 1. 描述关键词匹配
    desc_words = set(re.findall(r"\w+", mem.description.lower()))
    desc_overlap = len(query_words & desc_words)
    if desc_overlap > 0:
        score += desc_overlap * 2.0

    # 2. 文件名关键词匹配
    name_words = set(re.findall(r"\w+", mem.filename.lower()))
    name_overlap = len(query_words & name_words)
    if name_overlap > 0:
        score += name_overlap * 1.5

    # 3. 类型权重
    if mem.mem_type == MemoryType.FEEDBACK:
        score += 0.5  # 用户行为偏好优先
    elif mem.mem_type == MemoryType.PROJECT:
        score += 0.3  # 项目背景次之
    elif mem.mem_type == MemoryType.USER:
        score += 0.2  # 用户信息
    elif mem.mem_type == MemoryType.REFERENCE:
        score += 0.1  # 外部引用最低

    # 4. 新鲜度权重
    if mem.age == "today":
        score += 1.0
    elif mem.age == "yesterday":
        score += 0.5
    elif "days ago" in mem.age:
        try:
            days = int(mem.age.split()[0])
            if days <= 7:
                score += 0.2
        except (ValueError, IndexError):
            pass

    return score
