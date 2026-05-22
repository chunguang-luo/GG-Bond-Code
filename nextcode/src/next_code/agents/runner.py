"""runAgent() — 子 Agent 的完整生命周期引擎。

6 个阶段：
  Phase 1: 初始化     — 解析模型、构建消息
  Phase 2: 权限/Prompt — 过滤工具、构建 System Prompt
  Phase 3: MCP 初始化  — 加载 Agent 专属 MCP 服务器（后续迭代）
  Phase 4: Context 隔离 — createSubagentContext()
  Phase 5: 对话循环    — QueryRunner 循环 + 消息记录
  Phase 6: 清理       — 释放资源

用 async generator 逐步 yield QueryEvent，
让父 Agent（和 IPCBridge）可以实时看到子 Agent 的输出。
"""

from __future__ import annotations

import logging
import uuid
from typing import AsyncGenerator

from .definition import AgentDefinition
from .prompts import build_agent_system_prompt
from ..query import QueryRunner, QueryEvent
from ..state.context import ToolUseContext, create_subagent_context
from ..state.store import Store
from ..state.transition import LoopState
from ..tools.agent_filter import resolve_agent_tools

logger = logging.getLogger(__name__)


async def run_agent(
    agent_def: AgentDefinition,
    prompt: str,
    parent_context: ToolUseContext,
    *,
    fork_messages: list[dict] | None = None,
    is_async: bool = False,
) -> AsyncGenerator[QueryEvent, None]:
    """运行子 Agent 的完整生命周期。

    Args:
        agent_def: Agent 定义
        prompt: 用户给子 Agent 的指令
        parent_context: 父 Agent 的上下文
        fork_messages: Fork 模式下继承的对话历史
        is_async: 是否异步运行

    Yields:
        子 Agent 产生的每条 QueryEvent
    """
    agent_id = f"agent-{uuid.uuid4().hex[:8]}"

    # ── Phase 1: 初始化 ──────────────────────────────────────────

    # 解析模型：Agent 定义 > 父级模型 > 默认
    model = agent_def.model
    if model is None or model == "inherit":
        model = parent_context.get_state("model") or "deepseek-chat"

    # 构建初始消息
    if fork_messages is not None:
        initial_messages = _filter_incomplete_tool_calls(fork_messages)
    else:
        initial_messages = []

    # ── Phase 2: 权限与工具 ──────────────────────────────────────

    all_tools = parent_context.registry.all_tools()
    available_tools = resolve_agent_tools(
        agent_def, all_tools, is_async=is_async
    )
    available_tool_names = [t.name for t in available_tools]

    system_prompt = build_agent_system_prompt(agent_def, parent_context)

    # Load Agent Memory if memory_scope is set
    if agent_def.memory_scope:
        from ..memory.agent_memory import load_agent_memory_prompt

        mem_prompt = load_agent_memory_prompt(
            agent_def.memory_scope,
            agent_def.agent_type,
            parent_context.get_state("cwd"),
        )
        if mem_prompt:
            system_prompt = f"{system_prompt}\n\n{mem_prompt}"

    # ── Phase 4: Context 隔离 ──────────────────────────────────

    agent_context = create_subagent_context(
        parent_context,
        share_abort=(not is_async),
        share_set_state=(not is_async),
        share_metrics=True,
        agent_id=agent_id,
        agent_type=agent_def.agent_type,
        permission_mode=agent_def.permission_mode,
        allowed_tools=available_tool_names,
        avoid_permission_prompts=is_async,
    )

    # Set critical reminder for agents that need per-turn constraint reinforcement
    if agent_def.agent_type == "Verification":
        from .prompts.verification import VERIFICATION_CRITICAL_REMINDER
        agent_context.critical_reminder = VERIFICATION_CRITICAL_REMINDER

    # ── Phase 5: 对话循环 ──────────────────────────────────────

    # 用一个独立的 Store 存储子 Agent 的消息
    agent_store = Store()
    # 初始化消息历史
    if initial_messages:
        agent_store.set("messages", initial_messages)

    # Context optimization flags — read by QueryRunner.run()
    agent_store.set("omit_nextcode_md", agent_def.omit_nextcode_md)
    agent_store.set("omit_git_status", agent_def.omit_git_status)

    # 创建子 Agent 专用的 QueryRunner
    max_turns = agent_def.max_turns or 50
    runner = QueryRunner(
        model=model,
        context=agent_context,
        max_turns=max_turns,
        enable_compaction=False,  # 子 Agent 不做 compaction
        enable_streaming_tools=True,
    )
    # 覆盖 system prompt
    runner.system_prompt = [system_prompt]

    # Yield agent_start event
    yield QueryEvent(
        type="agent_start",
        content="",
        metadata={
            "agent_id": agent_id,
            "agent_type": agent_def.agent_type,
            "description": agent_def.description,
        },
        source="agent",
    )

    # 收集最终的 assistant 文本
    final_text_parts: list[str] = []
    tool_use_count = 0

    try:
        async for event in runner.run(prompt):
            # Tag all sub-agent events with source="agent"
            event.source = "agent"
            # Count tool calls for the result summary
            if event.type == "tool_use":
                tool_use_count += 1
            # 收集 assistant 的文本输出
            if event.type == "text":
                final_text_parts.append(event.content)
            yield event
    except Exception as e:
        logger.error("Agent %s (%s) failed: %s", agent_id, agent_def.agent_type, e)
        yield QueryEvent(type="error", content=str(e), source="agent")
    finally:
        # ── Phase 6: 清理 ──────────────────────────────────────
        await _cleanup_agent(agent_id, agent_context)

    # Yield agent_result event
    result_text = "".join(final_text_parts).strip()
    yield QueryEvent(
        type="agent_result",
        content=result_text or "Agent completed with no output",
        metadata={
            "agent_id": agent_id,
            "agent_type": agent_def.agent_type,
            "tool_use_count": tool_use_count,
        },
        source="agent",
    )


def _filter_incomplete_tool_calls(messages: list[dict]) -> list[dict]:
    """过滤掉不完整的 tool_use（没有对应 tool_result 的）。

    Fork 时继承的对话中，最后一条 assistant message 可能有 tool_use
    但对应的 tool_result 还没生成（父 Agent 还没执行完）。
    API 不接受没有 tool_result 的 tool_use，会报错。
    """
    if not messages:
        return messages

    filtered: list[dict] = []
    # Track tool_use_ids that have results
    tool_result_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_result_ids.add(block.get("tool_use_id", ""))

    for msg in messages:
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, list):
                # Filter out tool_use blocks without matching results
                new_content = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        if block.get("id", "") in tool_result_ids:
                            new_content.append(block)
                        else:
                            continue  # skip incomplete tool_use
                    else:
                        new_content.append(block)
                # Only include if there's still meaningful content
                if new_content:
                    filtered.append({**msg, "content": new_content})
                continue
        filtered.append(msg)

    return filtered


async def _cleanup_agent(agent_id: str, context: ToolUseContext) -> None:
    """清理子 Agent 的资源。"""
    # 1. 释放文件状态缓存
    if hasattr(context, "file_cache") and context.file_cache is not None:
        context.file_cache.clear()

    # 2. 杀死后台 bash 任务 — 后续迭代
    # kill_shell_tasks_for_agent(agent_id, context)

    # 3. 清理 AppState 中的 todos — 后续迭代

    logger.debug("Agent %s cleaned up", agent_id)
