"""Circuit breaker for compaction — stops after consecutive failures.

Mirrors Claude Code's circuit breaker design: 3 consecutive compact
failures trip the breaker, preventing wasted API calls.

Background: Claude Code statistics showed 1,279 sessions had 50+
consecutive failures (up to 3,272), wasting ~250K API calls/day globally.
"""

from __future__ import annotations


class CompactCircuitBreaker:
    """Stop attempting compaction after N consecutive failures."""

    def __init__(self, max_failures: int = 3) -> None:
        self._max_failures = max_failures
        self._consecutive_failures = 0

    @property
    def is_open(self) -> bool:
        """Whether the breaker is tripped (compaction should be skipped)."""
        return self._consecutive_failures >= self._max_failures

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def record_success(self) -> None:
        """Record a successful compaction; reset the counter."""
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        """Record a failed compaction; increment the counter."""
        self._consecutive_failures += 1

    def reset(self) -> None:
        """Manually reset the breaker."""
        self._consecutive_failures = 0
