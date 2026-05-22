"""记忆新鲜度标记 — 预计算保证 Prompt Cache 稳定性。

为什么预计算而不是在渲染时计算？因为如果在每次 API 调用时重新计算，
"saved 3 days ago" 可能变成 "saved 4 days ago" —— 不同的字节会导致
Prompt Cache 失效。预计算保证了跨 turn 的字节稳定性。
"""

from __future__ import annotations

import time


def memory_age(mtime: float) -> str:
    """计算记忆的新鲜度标签。

    Args:
        mtime: 文件修改时间戳（秒）

    Returns:
        新鲜度标签字符串
    """
    days = _days_ago(mtime)
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _days_ago(mtime: float) -> int:
    """计算距今天数。"""
    now = time.time()
    diff = now - mtime
    return int(diff // 86400)
