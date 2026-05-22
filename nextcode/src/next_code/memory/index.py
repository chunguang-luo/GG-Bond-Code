"""MEMORY.md 索引管理。

索引文件 ≤200 行 / 25KB，双重截断保护。
每条索引一行：- [Title](file.md) — one-line hook。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .paths import get_auto_mem_path

logger = logging.getLogger(__name__)

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000


def get_entrypoint_path(cwd: str | None = None) -> str:
    """获取 MEMORY.md 索引文件路径。"""
    return str(Path(get_auto_mem_path(cwd)) / ENTRYPOINT_NAME)


def read_index(cwd: str | None = None) -> str | None:
    """读取 MEMORY.md 索引内容。"""
    path = get_entrypoint_path(cwd)
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (IOError, OSError):
        return None


def write_index(content: str, cwd: str | None = None) -> None:
    """写入 MEMORY.md 索引，带截断保护。"""
    content = truncate_entrypoint_content(content)
    path = get_entrypoint_path(cwd)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def truncate_entrypoint_content(content: str) -> str:
    """双重截断保护：先按行截断（200 行），再按字节截断（25KB）。"""
    # 1. 按行截断
    lines = content.split("\n")
    truncated = False
    if len(lines) > MAX_ENTRYPOINT_LINES:
        lines = lines[:MAX_ENTRYPOINT_LINES]
        truncated = True

    content = "\n".join(lines)

    # 2. 按字节截断
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_ENTRYPOINT_BYTES:
        # 在最后一个换行符处切割，避免行被截断一半
        cut = encoded[:MAX_ENTRYPOINT_BYTES]
        last_newline = cut.rfind(b"\n")
        if last_newline > 0:
            cut = cut[:last_newline]
        content = cut.decode("utf-8", errors="ignore")
        truncated = True

    if truncated:
        content += "\n\n> WARNING: Memory index truncated. Keep entries concise."

    return content


def add_index_entry(filename: str, title: str, hook: str, cwd: str | None = None) -> None:
    """向索引添加一条记录。

    格式：- [Title](file.md) — one-line hook
    """
    current = read_index(cwd) or ""
    entry = f"- [{title}]({filename}) — {hook}"

    # 检查是否已存在（按 filename 去重）
    lines = current.split("\n")
    for i, line in enumerate(lines):
        if f"]({filename})" in line:
            lines[i] = entry
            break
    else:
        lines.append(entry)

    write_index("\n".join(lines), cwd)


def remove_index_entry(filename: str, cwd: str | None = None) -> None:
    """从索引移除一条记录。"""
    current = read_index(cwd)
    if not current:
        return

    lines = current.split("\n")
    lines = [l for l in lines if f"]({filename})" not in l]
    write_index("\n".join(lines), cwd)