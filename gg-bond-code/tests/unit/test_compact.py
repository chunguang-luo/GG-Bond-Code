"""Tests for context compaction strategies."""

import pytest
from gg_bond_code.compact.strategy import (
    TokenCountStrategy,
    MessageCountStrategy,
    should_compact_messages,
    repair_tool_references,
)


@pytest.mark.asyncio
async def test_token_count_strategy_should_compact():
    """Test that TokenCountStrategy triggers compaction when token count exceeds threshold."""
    strategy = TokenCountStrategy(threshold_tokens=1000)
    messages = [
        {"role": "user", "content": "test message"},
        {"role": "assistant", "content": "test response"},
    ]

    # Should not compact when under threshold
    should_compact = await strategy.should_compact(messages, "test-model", 500, 8000)
    assert not should_compact

    # Should compact when over threshold
    should_compact = await strategy.should_compact(messages, "test-model", 1500, 8000)
    assert should_compact


@pytest.mark.asyncio
async def test_token_count_strategy_compact():
    """Test that TokenCountStrategy reduces message count."""
    strategy = TokenCountStrategy(threshold_tokens=1000)
    messages = [{"role": "user", "content": f"message {i}"} for i in range(10)]

    compacted, reason = await strategy.compact(messages, "test-model")
    assert len(compacted) < len(messages)
    assert "token limit" in reason.lower()


@pytest.mark.asyncio
async def test_message_count_strategy_should_compact():
    """Test that MessageCountStrategy triggers compaction when message count exceeds limit."""
    strategy = MessageCountStrategy(max_messages=5)
    messages = [{"role": "user", "content": f"message {i}"} for i in range(3)]

    # Should not compact when under limit
    should_compact = await strategy.should_compact(messages, "test-model", 0, 0)
    assert not should_compact

    # Should compact when over limit
    messages.extend([{"role": "user", "content": f"message {i}"} for i in range(3)])
    should_compact = await strategy.should_compact(messages, "test-model", 0, 0)
    assert should_compact


@pytest.mark.asyncio
async def test_message_count_strategy_compact():
    """Test that MessageCountStrategy keeps only last N messages."""
    strategy = MessageCountStrategy(max_messages=5)
    messages = [{"role": "user", "content": f"message {i}"} for i in range(10)]

    compacted, reason = await strategy.compact(messages, "test-model")
    assert len(compacted) == 5
    assert compacted[0]["content"] == "message 5"  # Should keep last 5
    assert "last 5 messages" in reason.lower()


@pytest.mark.asyncio
async def test_should_compact_messages_default_strategy():
    """Test that should_compact_messages uses MessageCountStrategy by default."""
    messages = [{"role": "user", "content": "test"}]
    should_compact, strategy = await should_compact_messages(messages, "test-model")
    assert isinstance(strategy, MessageCountStrategy)
    # Default max_messages is 10, so 1 message shouldn't trigger compaction
    assert not should_compact


@pytest.mark.asyncio
async def test_should_compact_messages_custom_strategy():
    """Test that should_compact_messages accepts custom strategy."""
    messages = [{"role": "user", "content": "test"}]
    custom_strategy = MessageCountStrategy(max_messages=1)
    messages.append({"role": "user", "content": "test2"})

    should_compact, strategy = await should_compact_messages(
        messages,
        "test-model",
        strategy=custom_strategy,
    )
    assert isinstance(strategy, MessageCountStrategy)
    assert should_compact


# --- repair_tool_references tests ---


def test_repair_no_orphans():
    """Messages with consistent tool references pass through unchanged."""
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_1", "name": "Read", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "file content"},
        ]},
    ]
    result = repair_tool_references(messages)
    assert result == messages


def test_repair_orphan_tool_result_anthropic():
    """Orphaned tool_result (no matching tool_use) is removed."""
    messages = [
        # tool_use for call_1 was truncated away
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "orphaned result"},
        ]},
        {"role": "user", "content": "next question"},
    ]
    result = repair_tool_references(messages)
    # The orphaned tool_result message should be removed entirely
    assert len(result) == 1
    assert result[0]["content"] == "next question"


def test_repair_orphan_tool_result_openai():
    """Orphaned tool message (OpenAI format) is removed."""
    messages = [
        {"role": "tool", "tool_call_id": "call_1", "content": "orphaned result"},
        {"role": "user", "content": "next question"},
    ]
    result = repair_tool_references(messages)
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_repair_orphan_tool_use_anthropic():
    """Orphaned tool_use block (no matching tool_result) is removed from assistant."""
    messages = [
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me read that file."},
            {"type": "tool_use", "id": "call_1", "name": "Read", "input": {}},
        ]},
        # tool_result for call_1 was truncated away
        {"role": "user", "content": "next question"},
    ]
    result = repair_tool_references(messages)
    # Assistant message should keep text block, remove orphaned tool_use
    assert len(result) == 2
    assistant = result[0]
    assert len(assistant["content"]) == 1
    assert assistant["content"][0]["type"] == "text"


def test_repair_orphan_tool_use_only_anthropic():
    """Assistant with only orphaned tool_use blocks is removed entirely."""
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_1", "name": "Read", "input": {}},
        ]},
        {"role": "user", "content": "next question"},
    ]
    result = repair_tool_references(messages)
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_repair_orphan_tool_calls_openai():
    """Orphaned tool_calls (OpenAI format) are removed from assistant."""
    messages = [
        {"role": "assistant", "content": "Let me check.", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
        ]},
        {"role": "user", "content": "next question"},
    ]
    result = repair_tool_references(messages)
    assert len(result) == 2
    assistant = result[0]
    assert "tool_calls" not in assistant
    assert assistant["content"] == "Let me check."


def test_repair_orphan_tool_calls_only_openai():
    """Assistant with only orphaned tool_calls (no text) is removed entirely."""
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
        ]},
        {"role": "user", "content": "next question"},
    ]
    result = repair_tool_references(messages)
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_repair_mixed_partial_orphans():
    """Only orphaned references are removed; valid ones are preserved."""
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_1", "name": "Read", "input": {}},
            {"type": "tool_use", "id": "call_2", "name": "Grep", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "read result"},
            # call_2 result was truncated — orphan
        ]},
        {"role": "user", "content": "next question"},
    ]
    result = repair_tool_references(messages)
    # call_2 tool_use should be removed from assistant
    assistant = result[0]
    tool_uses = [b for b in assistant["content"] if b.get("type") == "tool_use"]
    assert len(tool_uses) == 1
    assert tool_uses[0]["id"] == "call_1"


def test_compact_with_tool_references_anthropic():
    """Compaction repairs tool references after truncation (Anthropic format)."""
    strategy = MessageCountStrategy(max_messages=3)
    messages = [
        {"role": "user", "content": "question 1"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_1", "name": "Read", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "result"},
        ]},
        {"role": "assistant", "content": "answer 1"},
        {"role": "user", "content": "question 2"},
        {"role": "assistant", "content": "answer 2"},
    ]

    import asyncio
    compacted, reason = asyncio.run(strategy.compact(messages, "test-model"))
    # After compaction + repair, no orphaned tool references should exist
    tool_ids = set()
    result_ids = set()
    for msg in compacted:
        if msg["role"] == "assistant" and isinstance(msg.get("content"), list):
            for b in msg["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool_ids.add(b["id"])
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for b in msg["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    result_ids.add(b.get("tool_use_id"))
    assert tool_ids == result_ids
