"""Session Memory 核心逻辑 — 与 Compact 系统协同。

在 auto-compact 触发时：
1. 等待 Session Memory 提取完成（15 秒超时）
2. 使用 Session Memory 内容作为 compact 后的总结（免额外 API 调用）
3. 如果 Session Memory 不可用，回退到常规 compact 策略
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from .session_extract import SessionMemoryState
from .session_template import (
    MAX_TOTAL_SESSION_MEMORY_TOKENS,
    create_empty_session_memory,
)

logger = logging.getLogger(__name__)

# Compact 协同超时
COMPACT_WAIT_TIMEOUT = 15  # 秒


class SessionMemoryManager:
    """Session Memory 管理器 — 协调提取状态与 compact 系统。"""

    def __init__(self) -> None:
        self._state = SessionMemoryState()
        self._extract_lock = threading.Lock()
        self._extract_event = threading.Event()

    @property
    def state(self) -> SessionMemoryState:
        """获取提取状态（供外部查询和更新 tool call 计数）。"""
        return self._state

    @property
    def has_content(self) -> bool:
        """是否有可用的 Session Memory 内容。"""
        return self._state.session_memory_content is not None

    def get_content(self) -> str | None:
        """获取 Session Memory 内容。"""
        return self._state.session_memory_content

    def notify_extraction_complete(self, content: str) -> None:
        """通知提取完成（由提取 Agent 调用）。"""
        self._state.update_content(content)
        self._extract_event.set()

    def wait_for_extraction(self, timeout: float = COMPACT_WAIT_TIMEOUT) -> str | None:
        """等待 Session Memory 提取完成。

        Args:
            timeout: 最大等待秒数

        Returns:
            Session Memory 内容，或 None（超时或不可用时）。
        """
        if self.has_content:
            return self.get_content()

        # 等待提取完成
        self._extract_event.wait(timeout=timeout)

        return self.get_content()

    async def wait_for_extraction_async(
        self, timeout: float = COMPACT_WAIT_TIMEOUT
    ) -> str | None:
        """异步等待 Session Memory 提取完成。"""
        return await asyncio.get_event_loop().run_in_executor(
            None, self.wait_for_extraction, timeout
        )

    def build_compact_context(
        self, existing_summary: str | None = None
    ) -> str:
        """构建 compact 后的上下文。

        优先使用 Session Memory，不可用时使用现有摘要。

        Args:
            existing_summary: 现有的 compact 摘要（如果有）

        Returns:
            compact 后的上下文字符串。
        """
        session_content = self.get_content()

        if session_content:
            return f"[Session Memory — structured context from conversation]\n\n{session_content}"

        if existing_summary:
            return existing_summary

        # 都没有——返回空模板提示
        return "[Context was compacted. No session memory or summary available.]"

    def reset(self) -> None:
        """重置管理器（新会话时使用）。"""
        self._state.reset()
        self._extract_event.clear()
