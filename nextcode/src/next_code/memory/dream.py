"""Auto Dream — 记忆的后台巩固。

三重门控：
1. 时间门控：距上次巩固 ≥ 24 小时
2. 会话门控：此期间至少有 5 个新会话
3. 锁门控：无其他进程正在巩固（文件锁）

失败时回滚锁的 mtime，让时间门控重新通过。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from .paths import get_auto_mem_path

logger = logging.getLogger(__name__)

MIN_HOURS = 24
MIN_SESSIONS = 5
LOCK_FILE = ".dream-lock"
SESSION_COUNTER_FILE = ".session-count"


def is_dream_gate_open(cwd: str | None = None) -> bool:
    """检查三重门控是否通过。

    Returns:
        True 如果所有门控都通过，可以执行巩固。
    """
    # 1. 时间门控
    lock_path = _get_lock_path(cwd)
    if os.path.exists(lock_path):
        stat = os.stat(lock_path)
        hours_since = (time.time() - stat.st_mtime) / 3600
        if hours_since < MIN_HOURS:
            logger.debug(
                "[dream] Time gate closed: %.1f hours since last dream (min: %d)",
                hours_since,
                MIN_HOURS,
            )
            return False

    # 2. 会话门控
    session_count = _count_recent_sessions(cwd)
    if session_count < MIN_SESSIONS:
        logger.debug(
            "[dream] Session gate closed: %d sessions (min: %d)",
            session_count,
            MIN_SESSIONS,
        )
        return False

    # 3. 锁门控（检查是否有其他进程正在巩固）
    if _is_locked(cwd):
        logger.debug("[dream] Lock gate closed: another process is dreaming")
        return False

    return True


def record_session(cwd: str | None = None) -> None:
    """记录一次新的会话（用于会话门控计数）。"""
    counter_path = _get_session_counter_path(cwd)
    try:
        mem_dir = get_auto_mem_path(cwd)
        os.makedirs(mem_dir, exist_ok=True)

        sessions = _read_session_counter(counter_path)
        sessions.append(time.time())

        # 只保留最近的 100 个会话记录
        if len(sessions) > 100:
            sessions = sessions[-100:]

        with open(counter_path, "w", encoding="utf-8") as f:
            json.dump(sessions, f)
    except (IOError, OSError) as e:
        logger.debug("[dream] Failed to record session: %s", e)


async def run_dream(cwd: str | None = None) -> None:
    """执行记忆巩固。

    三重门控通过后执行，失败时回滚锁。
    """
    prior_mtime = _acquire_lock(cwd)
    try:
        # TODO: 使用 run_agent() 启动巩固 Agent
        # 整理和优化记忆文件：合并重复、删除过时、更新索引
        # 巩固 Agent 的权限与提取 Agent 类似
        logger.info("[dream] Starting memory consolidation")
        # ... 实际巩固逻辑 ...
        logger.info("[dream] Memory consolidation complete (placeholder)")
    except Exception:
        # 回滚锁的 mtime 让时间门控重新通过
        _rollback_lock(cwd, prior_mtime)
        raise


def _get_lock_path(cwd: str | None = None) -> str:
    """获取锁文件路径。"""
    return str(Path(get_auto_mem_path(cwd)) / LOCK_FILE)


def _get_session_counter_path(cwd: str | None = None) -> str:
    """获取会话计数器文件路径。"""
    return str(Path(get_auto_mem_path(cwd)) / SESSION_COUNTER_FILE)


def _is_locked(cwd: str | None = None) -> bool:
    """检查是否有其他进程正在巩固。

    通过检查锁文件的 mtime 是否在最近 30 分钟内判断。
    正常巩固应该几分钟内完成，30 分钟还没完成说明出了问题。
    """
    lock_path = _get_lock_path(cwd)
    if not os.path.exists(lock_path):
        return False

    # 如果锁文件超过 30 分钟，认为锁已过期
    stat = os.stat(lock_path)
    minutes_since = (time.time() - stat.st_mtime) / 60
    return minutes_since < 30


def _acquire_lock(cwd: str | None = None) -> float | None:
    """获取锁，返回之前的 mtime（用于回滚）。

    Returns:
        锁文件之前的 mtime，或 None（文件不存在时）。
    """
    lock_path = _get_lock_path(cwd)
    prior_mtime = None

    if os.path.exists(lock_path):
        prior_mtime = os.stat(lock_path).st_mtime

    # 创建/更新锁文件
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as f:
        f.write(f"locked at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    return prior_mtime


def _rollback_lock(cwd: str | None = None, prior_mtime: float | None = None) -> None:
    """回滚锁的 mtime。

    巩固失败时调用，让时间门控重新通过。
    """
    lock_path = _get_lock_path(cwd)
    if os.path.exists(lock_path) and prior_mtime is not None:
        os.utime(lock_path, (prior_mtime, prior_mtime))
        logger.debug("[dream] Rolled back lock mtime to %s", prior_mtime)


def _count_recent_sessions(cwd: str | None = None) -> int:
    """计算自上次巩固以来的新会话数。"""
    counter_path = _get_session_counter_path(cwd)
    sessions = _read_session_counter(counter_path)

    if not sessions:
        return 0

    # 找到上次巩固时间
    lock_path = _get_lock_path(cwd)
    if os.path.exists(lock_path):
        last_dream_time = os.stat(lock_path).st_mtime
        # 只计数巩固之后的会话
        recent = [s for s in sessions if s > last_dream_time]
        return len(recent)

    # 没有锁文件，所有会话都算
    return len(sessions)


def _read_session_counter(path: str) -> list[float]:
    """读取会话计数器。"""
    try:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (IOError, OSError, json.JSONDecodeError):
        return []
