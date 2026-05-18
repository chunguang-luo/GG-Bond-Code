"""Tests for compact manager — multi-level compaction orchestrator."""

import pytest

from next_code.compact.manager import CompactManager, CompactLevel
from next_code.compact.budget import get_auto_compact_threshold, get_effective_context_window, BLOCKING_BUFFER_TOKENS


def test_evaluate_none_level():
    """Low token usage → NONE."""
    manager = CompactManager(model="claude-sonnet-4-20250514")
    level, state = manager.evaluate(1_000)
    assert level == CompactLevel.NONE
    assert not state.is_above_warning


def test_evaluate_micro_level():
    """Usage above warning but below auto-compact → MICRO."""
    manager = CompactManager(model="claude-sonnet-4-20250514")
    threshold = get_auto_compact_threshold("claude-sonnet-4-20250514")
    # Just above warning threshold but below auto-compact
    usage = threshold - 10_000
    level, state = manager.evaluate(usage)
    assert level == CompactLevel.MICRO
    assert state.is_above_warning
    assert not state.is_above_auto_compact


def test_evaluate_full_level():
    """Usage at auto-compact threshold → FULL."""
    manager = CompactManager(model="claude-sonnet-4-20250514")
    threshold = get_auto_compact_threshold("claude-sonnet-4-20250514")
    level, state = manager.evaluate(threshold)
    assert level == CompactLevel.FULL
    assert state.is_above_auto_compact


def test_evaluate_blocking_level():
    """Usage at blocking limit → BLOCKING."""
    manager = CompactManager(model="claude-sonnet-4-20250514")
    effective = get_effective_context_window("claude-sonnet-4-20250514")
    blocking = effective - BLOCKING_BUFFER_TOKENS
    level, state = manager.evaluate(blocking + 1000)
    assert level == CompactLevel.BLOCKING
    assert state.is_at_blocking


def test_evaluate_full_degrades_to_micro_on_circuit_open():
    """When circuit breaker is open, FULL degrades to MICRO."""
    manager = CompactManager(model="claude-sonnet-4-20250514")
    # Trip the circuit breaker
    manager._full_strategy.circuit_breaker.record_failure()
    manager._full_strategy.circuit_breaker.record_failure()
    manager._full_strategy.circuit_breaker.record_failure()
    assert manager._full_strategy.circuit_breaker.is_open

    threshold = get_auto_compact_threshold("claude-sonnet-4-20250514")
    level, state = manager.evaluate(threshold)
    assert level == CompactLevel.MICRO


@pytest.mark.asyncio
async def test_execute_none_returns_unchanged():
    """NONE level returns messages unchanged."""
    manager = CompactManager(model="claude-sonnet-4-20250514")
    messages = [{"role": "user", "content": "Hello"}]
    result, reason = await manager.execute(CompactLevel.NONE, messages)
    assert result == messages


@pytest.mark.asyncio
async def test_execute_blocking_returns_unchanged():
    """BLOCKING level returns messages unchanged with reason."""
    manager = CompactManager(model="claude-sonnet-4-20250514")
    messages = [{"role": "user", "content": "Hello"}]
    result, reason = await manager.execute(CompactLevel.BLOCKING, messages)
    assert result == messages
    assert "full" in reason.lower()


def test_get_token_usage():
    """get_token_usage returns a positive integer for non-empty messages."""
    manager = CompactManager(model="claude-sonnet-4-20250514")
    messages = [{"role": "user", "content": "Hello world"}]
    tokens = manager.get_token_usage(messages)
    assert tokens > 0
