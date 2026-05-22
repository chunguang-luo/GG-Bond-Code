"""Memory 目录管理 — 确保目录存在的承诺。

代码保证前置条件，Prompt 告知 AI 前置条件已满足（DIR_EXISTS_GUIDANCE），
从而省去不必要的验证步骤。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .paths import get_auto_mem_path

logger = logging.getLogger(__name__)

DIR_EXISTS_GUIDANCE = (
    "This directory already exists — write to it directly with the Write tool "
    "(do not run mkdir or check for its existence)."
)


def ensure_memory_dir_exists(cwd: str | None = None) -> str:
    """确保记忆目录存在，返回路径。

    代码层面的保证——与 DIR_EXISTS_GUIDANCE Prompt 协同。
    """
    mem_dir = get_auto_mem_path(cwd)
    os.makedirs(mem_dir, exist_ok=True)
    return mem_dir


def get_memory_files(cwd: str | None = None) -> list[str]:
    """列出记忆目录中的所有 .md 文件（排除 MEMORY.md 索引）。"""
    mem_dir = get_auto_mem_path(cwd)
    if not os.path.isdir(mem_dir):
        return []

    files = []
    for entry in os.listdir(mem_dir):
        if entry.endswith(".md") and entry != "MEMORY.md":
            files.append(str(Path(mem_dir) / entry))

    return sorted(files)