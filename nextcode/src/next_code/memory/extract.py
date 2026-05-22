"""后台记忆提取 — 从对话中自动提取记忆。

核心设计：
- Closure-scoped 状态管理（游标、互斥锁、暂存上下文）
- 合并模式：不排队所有请求，只保留最新的一个
- 与主 Agent 互斥：主 Agent 已写入记忆时跳过
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .paths import get_auto_mem_path
from .index import read_index
from .types import MEMORY_TYPES, TYPE_META, WHAT_NOT_TO_SAVE, build_frontmatter

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

    # ── 构建提取 Agent 定义 ──────────────────────────────────────

    from ..agents.definition import AgentDefinition, AgentSource

    agent_def = AgentDefinition(
        agent_type="memory-extract",
        name="Memory Extract",
        description="Extracts valuable information from conversation into memory files.",
        source=AgentSource.BUILTIN,
        tools=["Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit"],  # 读写工具
        disallowed_tools=[
            "Agent",
            "MCP",
            "TaskStop",
            "TaskOutput",
            "AskUserQuestion",
            "EnterPlanMode",
            "ExitPlanMode",
            "WebSearch",
            "WebFetch",
        ],
        max_turns=5,
        memory_scope=None,  # 不需要 Agent Memory
        omit_nextcode_md=True,
        omit_git_status=True,
        get_system_prompt=_build_extract_agent_system_prompt,
    )

    # ── 构建提取 Prompt ──────────────────────────────────────────

    prompt = _build_extraction_prompt(messages, context)

    # ── 运行 Agent（同步收集结果，不 yield）───────────────────────
    # 注意：这里在后台任务中运行，不需要 yield 事件给父 Agent

    try:
        from ..agents.runner import run_agent

        results: list[str] = []

        async for event in run_agent(
            agent_def,
            prompt,
            context,
            is_async=True,
        ):
            if event.type == "text":
                results.append(event.content)

        if results:
            logger.info(
                "[extractMemories] extraction completed, final output: %s",
                "".join(results)[:200],
            )
        else:
            logger.info("[extractMemories] extraction completed with no output")

    except Exception as e:
        logger.error("[extractMemories] extraction failed: %s", e)


def _build_extract_agent_system_prompt() -> str:
    """构建提取 Agent 的 System Prompt。"""
    type_info = "\n".join(
        f"- **{t.value}**: {TYPE_META[t]['meaning']} | "
        f"write when: {TYPE_META[t]['write_when']} | "
        f"don't save: {TYPE_META[t]['dont_save']}"
        for t in MEMORY_TYPES
    )

    return f"""You are a memory extraction agent. Your job is to review the conversation and extract information worth remembering.

## Memory Types
{type_info}

## What NOT to Save
{chr(10).join(f"- {w}" for w in WHAT_NOT_TO_SAVE)}

## Strategy
- Turn 1: Issue all FileRead calls in parallel for every file you might update
- Turn 2: Issue all FileWrite/FileEdit calls in parallel

## CRITICAL: Write Permissions
- You can ONLY use Edit, Write, and NotebookEdit tools on files inside the memory directory shown above
- You MUST NOT write to any other directories
- Violating this rule will cause errors

## Rules
- Each memory file gets frontmatter with name, description, type
- After writing a memory file, update the MEMORY.md index with a one-line pointer
- Be conservative — it's better to miss a memory than to save noise
- You have at most 5 turns — do not go down verification rabbit holes
"""


def _build_extraction_prompt(messages: list[dict], context: Any) -> str:
    """构建提取指令，包含对话摘要和现有记忆文件列表。"""
    import json

    mem_dir = get_auto_mem_path()

    # ── 对话摘要 ──────────────────────────────────────────────────

    # 提取对话的核心内容（限制长度）
    conversation_text = _summarize_conversation(messages)

    # ── 现有记忆文件列表 ──────────────────────────────────────────

    existing_memories = []
    if os.path.isdir(mem_dir):
        for entry in sorted(os.listdir(mem_dir)):
            if entry.endswith(".md") and entry != "MEMORY.md":
                filepath = os.path.join(mem_dir, entry)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                    # 提取 frontmatter
                    from .types import parse_frontmatter

                    meta = parse_frontmatter(content)
                    existing_memories.append({
                        "filename": entry,
                        "name": meta.get("name", entry[:-3]),
                        "type": meta.get("type", "unknown"),
                    })
                except (IOError, OSError):
                    continue

    # ── 构建 Prompt ───────────────────────────────────────────────

    parts = [
        f"# Memory Extraction Task",
        "",
        f"Memory directory: `{mem_dir}/`",
        f"(This directory already exists — write to it directly with the Write tool.)",
        "",
        "## Existing Memory Files",
    ]

    if existing_memories:
        for mem in existing_memories:
            parts.append(f"- {mem['filename']} ({mem['type']})")
    else:
        parts.append("(No existing memory files)")

    parts.extend([
        "",
        "## Conversation to Analyze",
        conversation_text,
        "",
        "## Your Task",
        "Extract information worth remembering from the conversation above.",
        "Follow the rules in your system prompt.",
        "Create memory files for user preferences, feedback patterns, project context, or external references.",
        "Update MEMORY.md index after each write.",
        "",
        "Be selective. If nothing worth remembering, simply respond 'No memories to extract.'",
    ])

    return "\n".join(parts)


def _summarize_conversation(messages: list[dict]) -> str:
    """从对话中提取核心内容（用于提取 Agent）。"""
    lines = []

    for msg in messages[-20:]:  # 只取最近 20 条消息
        role = msg.get("role", "")
        content = msg.get("content", "")

        # 处理 content 块（可能包含 text 和 tool_result）
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    # 跳过 tool_result，避免冗长
            content = " ".join(text_parts)
        elif not isinstance(content, str):
            content = str(content)

        # 跳过空消息和过长的内容
        if not content or len(content) > 2000:
            continue

        # 简化用户消息
        if role == "user":
            # 只取前 200 字符
            preview = content[:200]
            if len(content) > 200:
                preview += "..."
            lines.append(f"User: {preview}")
        elif role == "assistant":
            # 检查是否有 tool_use
            has_tool = False
            if isinstance(msg.get("content"), list):
                has_tool = any(
                    b.get("type") == "tool_use"
                    for b in msg.get("content", [])
                    if isinstance(b, dict)
                )
            if has_tool:
                lines.append("Assistant: [used tools]")
            else:
                preview = content[:150]
                if len(content) > 150:
                    preview += "..."
                lines.append(f"Assistant: {preview}")

    result = "\n".join(lines)

    # 限制总长度
    if len(result) > 4000:
        result = result[:4000] + "\n... (truncated)"

    return result
