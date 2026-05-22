"""Session Memory 提取触发 — 双阈值控制。

触发条件：
1. Token 阈值（必要条件）：自上次提取后增长 ≥ 5000 tokens
2. Tool call 阈值：≥ 3 次 tool calls
3. 自然暂停：无 tool call 的 turn

触发 = token 阈值 AND (tool call 阈值 OR 自然暂停)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 默认阈值
MINIMUM_MESSAGE_TOKENS_TO_INIT = 10000  # 初始化阈值
MINIMUM_TOKENS_BETWEEN_UPDATE = 5000  # 更新 token 增长阈值
TOOL_CALLS_BETWEEN_UPDATES = 3  # 更新 tool call 阈值


class SessionMemoryState:
    """Session Memory 提取状态管理。"""

    def __init__(self) -> None:
        self._initialized = False
        self._last_token_count = 0
        self._tool_calls_since_update = 0
        self._session_memory_content: str | None = None

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def session_memory_content(self) -> str | None:
        """获取当前的 Session Memory 内容。"""
        return self._session_memory_content

    def update_content(self, content: str) -> None:
        """更新 Session Memory 内容。"""
        self._session_memory_content = content

    def mark_initialized(self) -> None:
        self._initialized = True

    def should_extract(
        self,
        current_token_count: int,
        has_tool_calls_in_last_turn: bool,
    ) -> bool:
        """判断是否应该触发提取。

        Args:
            current_token_count: 当前 token 计数
            has_tool_calls_in_last_turn: 上一轮是否有 tool call

        Returns:
            是否应该触发提取。
        """
        # 1. 初始化阈值
        if not self._initialized:
            if current_token_count < MINIMUM_MESSAGE_TOKENS_TO_INIT:
                return False
            self._initialized = True

        # 2. Token 增长阈值（必要条件）
        token_growth = current_token_count - self._last_token_count
        has_met_token_threshold = token_growth >= MINIMUM_TOKENS_BETWEEN_UPDATE

        # 3. Tool call 计数阈值
        has_met_tool_call_threshold = (
            self._tool_calls_since_update >= TOOL_CALLS_BETWEEN_UPDATES
        )

        # 触发条件
        should = (has_met_token_threshold and has_met_tool_call_threshold) or (
            has_met_token_threshold and not has_tool_calls_in_last_turn
        )

        if should:
            self._last_token_count = current_token_count
            self._tool_calls_since_update = 0

        return should

    def record_tool_call(self) -> None:
        """记录一次 tool call。"""
        self._tool_calls_since_update += 1

    def reset(self) -> None:
        """重置状态（新会话时使用）。"""
        self._initialized = False
        self._last_token_count = 0
        self._tool_calls_since_update = 0
        self._session_memory_content = None
