"""Tests for full compact — model-summarized compression."""

import pytest

from gg_bond_code.compact.full import (
    FullCompactStrategy,
    _calculate_messages_to_keep,
)
from gg_bond_code.compact.circuit_breaker import CompactCircuitBreaker


@pytest.mark.asyncio
async def test_full_compact_should_compact_above_threshold():
    """Token usage above auto-compact threshold triggers compaction."""
    strategy = FullCompactStrategy()
    # Use a model with known threshold, pass very high token count
    should = await strategy.should_compact([], "claude-sonnet-4-20250514", 999_999, 0)
    assert should is True


@pytest.mark.asyncio
async def test_full_compact_should_not_compact_below_threshold():
    """Low token usage does not trigger compaction."""
    strategy = FullCompactStrategy()
    should = await strategy.should_compact([], "claude-sonnet-4-20250514", 100, 0)
    assert should is False


@pytest.mark.asyncio
async def test_full_compact_should_not_compact_circuit_open():
    """When circuit breaker is open, should_compact returns False."""
    strategy = FullCompactStrategy(max_failures=1)
    strategy.circuit_breaker.record_failure()
    assert strategy.circuit_breaker.is_open
    should = await strategy.should_compact([], "claude-sonnet-4-20250514", 999_999, 0)
    assert should is False


@pytest.mark.asyncio
async def test_full_compact_circuit_breaker_failure():
    """Failed compact increments circuit breaker."""
    strategy = FullCompactStrategy(max_failures=3)
    # compact() will fail because stream_message needs real API
    messages = [{"role": "user", "content": "test"}]
    result_messages, reason = await strategy.compact(messages, "unknown-model-xyz")
    # Should return original messages on failure
    assert result_messages == messages
    assert "failed" in reason.lower() or "empty" in reason.lower()
    assert strategy.circuit_breaker.consecutive_failures >= 1


def test_calculate_messages_to_keep_small():
    """Small message list returns min_messages (clamped up)."""
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(3)]
    count = _calculate_messages_to_keep(messages, min_messages=5)
    # min_messages=5 > len(messages)=3, but max(min, count) = 5
    # However min(max_messages, len)=3, then max(min, 3)=5
    assert count == 5  # min_messages clamps up


def test_calculate_messages_to_keep_large():
    """Large message list is capped at max_messages."""
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(50)]
    count = _calculate_messages_to_keep(messages, max_messages=20)
    assert count == 20


def test_calculate_messages_to_keep_respects_min():
    """Count doesn't go below min_messages."""
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(50)]
    count = _calculate_messages_to_keep(messages, min_messages=5, max_messages=20)
    assert count >= 5
