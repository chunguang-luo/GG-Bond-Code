"""后台记忆提取 — 从对话中自动提取记忆。

核心设计：
- Closure-scoped 状态管理（游标、互斥锁、暂存上下文）
- 合并模式：不排队所有请求，只保留最新的一个
- 与主 Agent 互斥：主 Agent 已写入记忆时跳过
"""

from __future__ import annotations

import logging
from typing import Any

from .paths import get_auto_mem_path

logger = logging.getLogger(__name__)

# Closure-scoped 状态
_last_memory_message_uuid: str | None = None
_in_progress: bool = False
_pending_context: dict | None = None


def init_extract_memories() -> None:
    """初始化提取器（闭包重置）。"""
    global _last_memory_message_uuid, _in_progress, _pending_context
    _last_memory_message_uuid = None
    _in_progress = False
    _pending_context = None


async def execute_extract_memories(
    messages: list[dict],
    context: Any,
) -> None:
    """执行记忆提取（fire-and-forget）。

    Args:
        messages: 完整对话消息列表
        context: ToolUseContext 或等效上下文对象
    """
    global _in_progress, _pending_context

    # 互斥：如果提取进行中，暂存最新上下文
    if _in_progress:
        logger.debug("[extractMemories] extraction in progress — stashing for trailing run")
        _pending_context = {"messages": messages, "context": context}
        return

    # 互斥：主 Agent 已写入记忆时跳过
    if _has_memory_writes_since(messages, _last_memory_message_uuid):
        logger.debug("[extractMemories] skipping — conversation already wrote to memory files")
        _update_cursor(messages)
        return

    _in_progress = True
    try:
        await _run_extraction(messages, context)
    finally:
        _in_progress = False
        # Trailing run
        if _pending_context is not None:
            pending = _pending_context
            _pending_context = None
            await execute_extract_memories(pending["messages"], pending["context"])


def _has_memory_writes_since(messages: list[dict], since_uuid: str | None) -> bool:
    """检查主 Agent 是否已写入记忆文件。"""
    mem_dir = get_auto_mem_path()

    found_cursor = since_uuid is None
    for msg in messages:
        if not found_cursor:
            if msg.get("uuid") == since_uuid:
                found_cursor = True
            continue

        # 检查 assistant 消息中的 tool_use
        if msg.get("role") == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        # 检查 Edit/Write 目标路径是否在 memory 目录内
                        input_data = block.get("input", {})
                        file_path = input_data.get("file_path", input_data.get("path", ""))
                        if file_path.startswith(mem_dir):
                            return True
    return False


def _update_cursor(messages: list[dict]) -> None:
    """更新游标到最新消息。"""
    global _last_memory_message_uuid
    if messages:
        last = messages[-1]
        uuid = last.get("uuid")
        if uuid:
            _last_memory_message_uuid = uuid


async def _run_extraction(messages: list[dict], context: Any) -> None:
    """运行提取 Agent（forked sub-agent，权限沙箱）。

    权限沙箱：
    - 允许：FileRead, Grep, Glob（任意路径，只读）
    - 允许：Bash（仅只读命令）
    - 允许：FileEdit/FileWrite（仅限 memoryDir 内）
    - 拒绝：MCP, Agent, 写入型 Bash
    - maxTurns: 5
    """
    # 更新游标
    _update_cursor(messages)

    # TODO: 使用 run_agent() 启动提取 Agent
    # 当前实现为占位——需要 Agent 系统的 run_agent() 接口
    # 预期行为：
    # 1. 创建 AgentDefinition(
    #      agent_type="memory-extract",
    #      tools=["FileRead", "Grep", "Glob", "FileEdit", "FileWrite"],
    #      disallowed_tools=["Agent", "MCP"],
    #      max_turns=5,
    #   )
    # 2. 构建提取 prompt（包含对话摘要 + 现有记忆文件列表）
    # 3. run_agent(agent_def, messages=[...], context=context)
    logger.debug("[extractMemories] extraction placeholder — will be implemented with Agent system")
