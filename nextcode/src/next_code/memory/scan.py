"""记忆文件扫描 — 双阶段召回的 Scan 阶段。

并行读取每个记忆文件的 frontmatter（前 30 行），
提取 name/description/type/age，为 Select 阶段提供候选列表。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .age import memory_age
from .types import MemoryType, parse_frontmatter

logger = logging.getLogger(__name__)

FRONTMATTER_MAX_LINES = 30
MAX_MEMORY_FILES = 200


@dataclass
class MemoryHeader:
    """一个记忆文件的扫描头信息。"""

    filename: str
    filepath: str
    description: str
    mem_type: MemoryType | None
    age: str


def scan_memory_files(memory_dir: str) -> list[MemoryHeader]:
    """扫描记忆目录中的所有文件头。

    按修改时间降序排序，最多返回 MAX_MEMORY_FILES 个。

    Args:
        memory_dir: 记忆目录路径

    Returns:
        扫描到的记忆头列表
    """
    if not os.path.isdir(memory_dir):
        return []

    entries = []

    for entry in os.listdir(memory_dir):
        if entry.endswith(".md") and entry != "MEMORY.md":
            filepath = os.path.join(memory_dir, entry)
            try:
                stat = os.stat(filepath)
                entries.append((entry, filepath, stat.st_mtime))
            except (IOError, OSError):
                continue

    # 按修改时间降序排序，最多 200 个
    entries.sort(key=lambda x: x[2], reverse=True)
    entries = entries[:MAX_MEMORY_FILES]

    results = []
    for filename, filepath, mtime in entries:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                # 只读前 30 行
                lines = []
                for i, line in enumerate(f):
                    if i >= FRONTMATTER_MAX_LINES:
                        break
                    lines.append(line)
                content = "".join(lines)

            meta = parse_frontmatter(content, filepath)
            results.append(
                MemoryHeader(
                    filename=filename,
                    filepath=filepath,
                    description=meta.get("description", ""),
                    mem_type=meta.get("type"),
                    age=memory_age(mtime),
                )
            )
        except (IOError, OSError):
            continue

    return results
