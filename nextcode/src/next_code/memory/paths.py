"""Memory 路径解析 — 3 级优先级。

1. 环境变量覆盖：NEXTCODE_MEMORY_PATH_OVERRIDE
2. Settings 覆盖：autoMemoryDirectory（仅信任 policy/flag/local/user，排除 projectSettings）
3. 默认路径：基于 Git 根目录的 sanitized 路径
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path


def sanitize_path(path: str) -> str:
    """将路径中的非字母数字字符替换为连字符。

    Example: '/Users/foo/my-project' → '-Users-foo-my-project'
    """
    return re.sub(r'[^a-zA-Z0-9]', '-', path)


def find_git_root(start: str) -> str | None:
    """向上查找 .git 目录。"""
    current = Path(start).resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return str(current)
        current = current.parent
    return None


def get_memory_base_dir() -> str:
    """获取记忆基础目录（~/.nextcode/）。"""
    return str(Path.home() / ".nextcode")


@lru_cache(maxsize=1)
def get_auto_mem_path(cwd: str | None = None) -> str:
    """获取 Auto Memory 目录路径。

    三级优先级：
    1. NEXTCODE_MEMORY_PATH_OVERRIDE 环境变量
    2. autoMemoryDirectory settings（排除 projectSettings）
    3. 基于 Git 根目录的默认路径
    """
    # 1. 环境变量覆盖
    override = os.environ.get("NEXTCODE_MEMORY_PATH_OVERRIDE")
    if override:
        return override

    # 2. Settings 覆盖（TODO: 从 settings 读取，排除 projectSettings）

    # 3. 默认路径
    base = cwd or os.getcwd()
    git_root = find_git_root(base)
    mem_base = git_root or base
    sanitized = sanitize_path(mem_base)

    return str(Path(get_memory_base_dir()) / "projects" / sanitized / "memory")


def clear_path_cache() -> None:
    """清除路径缓存（切换项目时使用）。"""
    get_auto_mem_path.cache_clear()