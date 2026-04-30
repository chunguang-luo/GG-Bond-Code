"""Tests for context compaction strategies."""

import pytest
from gg_bond_code.compact.strategy import (
    TokenCountStrategy,
    MessageCountStrategy,
    should_compact_messages,
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
