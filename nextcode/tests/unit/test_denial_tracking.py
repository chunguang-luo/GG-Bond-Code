"""Tests for permissions/denial_tracking.py — circuit breaker logic."""

import pytest
from next_code.permissions.denial_tracking import (
    DenialTrackingState,
    record_denial,
    record_success,
    should_fallback_to_prompting,
    reset_denial_state,
    DENIAL_LIMITS,
)


class TestDenialTrackingState:
    def test_initial_state(self):
        state = DenialTrackingState()
        assert state.consecutive_denials == 0
        assert state.total_denials == 0

    def test_record_denial(self):
        state = DenialTrackingState()
        state = record_denial(state)
        assert state.consecutive_denials == 1
        assert state.total_denials == 1

    def test_record_multiple_denials(self):
        state = DenialTrackingState()
        for _ in range(5):
            state = record_denial(state)
        assert state.consecutive_denials == 5
        assert state.total_denials == 5

    def test_record_success_resets_consecutive(self):
        state = DenialTrackingState()
        state = record_denial(state)
        state = record_denial(state)
        assert state.consecutive_denials == 2
        state = record_success(state)
        assert state.consecutive_denials == 0
        assert state.total_denials == 2  # Total is NOT reset

    def test_record_success_noop_when_zero(self):
        state = DenialTrackingState()
        result = record_success(state)
        # Same instance returned (optimization)
        assert result is state

    def test_immutable_update(self):
        state = DenialTrackingState()
        new_state = record_denial(state)
        # Original unchanged
        assert state.consecutive_denials == 0
        assert new_state.consecutive_denials == 1


class TestCircuitBreaker:
    def test_no_fallback_initially(self):
        state = DenialTrackingState()
        assert not should_fallback_to_prompting(state)

    def test_fallback_on_consecutive_limit(self):
        state = DenialTrackingState()
        for _ in range(DENIAL_LIMITS["max_consecutive"]):
            state = record_denial(state)
        assert should_fallback_to_prompting(state)

    def test_fallback_on_total_limit(self):
        state = DenialTrackingState()
        for _ in range(DENIAL_LIMITS["max_total"]):
            # Interleave successes so consecutive never hits
            state = record_denial(state)
            state = record_success(state)
            state = record_denial(state)
            state = record_success(state)
        assert state.total_denials >= DENIAL_LIMITS["max_total"]
        assert should_fallback_to_prompting(state)

    def test_no_fallback_before_limit(self):
        state = DenialTrackingState()
        state = record_denial(state)
        state = record_denial(state)  # 2 consecutive — not yet at limit
        assert not should_fallback_to_prompting(state)

    def test_success_resets_fallback_threshold(self):
        state = DenialTrackingState()
        state = record_denial(state)
        state = record_denial(state)  # 2 consecutive
        state = record_success(state)  # reset
        state = record_denial(state)   # 1 consecutive
        assert not should_fallback_to_prompting(state)


class TestResetDenialState:
    def test_reset(self):
        state = DenialTrackingState(consecutive_denials=5, total_denials=10)
        new_state = reset_denial_state()
        assert new_state.consecutive_denials == 0
        assert new_state.total_denials == 0