"""Tests for compact circuit breaker."""

from gg_bond_code.compact.circuit_breaker import CompactCircuitBreaker


def test_initial_state_is_closed():
    cb = CompactCircuitBreaker(max_failures=3)
    assert not cb.is_open
    assert cb.consecutive_failures == 0


def test_record_failure_increments():
    cb = CompactCircuitBreaker(max_failures=3)
    cb.record_failure()
    assert cb.consecutive_failures == 1
    assert not cb.is_open


def test_opens_after_max_failures():
    cb = CompactCircuitBreaker(max_failures=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open
    assert cb.consecutive_failures == 3


def test_record_success_resets():
    cb = CompactCircuitBreaker(max_failures=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    assert cb.consecutive_failures == 0
    assert not cb.is_open


def test_reset_clears_failures():
    cb = CompactCircuitBreaker(max_failures=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open
    cb.reset()
    assert not cb.is_open
    assert cb.consecutive_failures == 0


def test_is_open_boundary():
    cb = CompactCircuitBreaker(max_failures=3)
    cb.record_failure()
    cb.record_failure()
    assert not cb.is_open  # 2 < 3
    cb.record_failure()
    assert cb.is_open  # 3 >= 3
