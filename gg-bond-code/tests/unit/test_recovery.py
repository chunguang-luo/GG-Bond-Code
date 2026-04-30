"""Tests for recovery strategies."""

import pytest
from gg_bond_code.api.recovery import (
    MaxOutputTokensRecovery,
    SurfaceErrorRecovery,
)


@pytest.mark.asyncio
async def test_max_output_tokens_recovery_should_recover():
    """Test that MaxOutputTokensRecovery should recover from token limit errors."""
    recovery = MaxOutputTokensRecovery(max_recovery_count=3)

    # Should recover from token limit errors
    error = Exception("max_output_tokens exceeded")
    assert await recovery.should_recover(error, 0)

    # Should recover from output_tokens_exceeded errors
    error = Exception("output_tokens_exceeded: limit reached")
    assert await recovery.should_recover(error, 0)

    # Should not recover from other errors
    error = Exception("internal server error")
    assert not await recovery.should_recover(error, 0)

    # Should not recover after max_recovery_count
    error = Exception("max_output_tokens exceeded")
    assert not await recovery.should_recover(error, 5)


@pytest.mark.asyncio
async def test_max_output_tokens_recovery_recover():
    """Test that MaxOutputTokensRecovery generates correct recovery message."""
    recovery = MaxOutputTokensRecovery(max_recovery_count=3)
    messages = [{"role": "user", "content": "test"}]
    error = Exception("max_output_tokens exceeded")

    recovery_message = await recovery.recover(messages, error)
    assert recovery_message["role"] == "user"
    assert "Resume directly" in recovery_message["content"]
    assert recovery_message.get("is_meta") is True
    assert recovery.recovery_count == 1


@pytest.mark.asyncio
async def test_surface_error_recovery_should_recover():
    """Test that SurfaceErrorRecovery should recover from surface errors."""
    recovery = SurfaceErrorRecovery()

    # Should recover from surface errors
    error = Exception("Error: API timeout")
    assert await recovery.should_recover(error, 0)

    # Should not recover from internal errors
    error = Exception("Internal server error")
    assert not await recovery.should_recover(error, 0)

    # Should recover regardless of recovery_count
    error = Exception("Error: connection refused")
    assert await recovery.should_recover(error, 100)


@pytest.mark.asyncio
async def test_surface_error_recovery_recover():
    """Test that SurfaceErrorRecovery generates correct recovery message."""
    recovery = SurfaceErrorRecovery()
    messages = [{"role": "user", "content": "test"}]
    error = Exception("Error: API timeout")

    recovery_message = await recovery.recover(messages, error)
    assert recovery_message["role"] == "user"
    assert "Error: API timeout" in recovery_message["content"]
    assert recovery_message.get("is_meta") is True
