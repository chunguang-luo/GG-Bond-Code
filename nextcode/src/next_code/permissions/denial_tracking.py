"""Denial tracking with circuit breaker — mirrors denialTracking.ts.

When the permission system repeatedly denies operations, the agent can get
stuck in an "attempt → deny → retry" loop. This module implements a circuit
breaker that tracks consecutive and total denials, then falls back to a
safer strategy:

- Interactive CLI: fall back to prompting the user (pop up confirmation dialog)
- Headless mode: abort the entire agent (no user available to approve)

Mirrors Claude Code's DENIAL_LIMITS:
- maxConsecutive: 3 consecutive denials → trigger fallback
- maxTotal: 20 total denials → trigger fallback
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DenialTrackingState:
    """Tracks denial counts for circuit breaker logic.

    consecutiveDenials: Count of consecutive denials (reset on success).
    totalDenials: Total denials across the entire session (never reset).
    """

    consecutive_denials: int = 0
    total_denials: int = 0


# Circuit breaker limits
DENIAL_LIMITS = {
    "max_consecutive": 3,
    "max_total": 20,
}


def record_denial(state: DenialTrackingState) -> DenialTrackingState:
    """Record a denial and return updated state.

    Returns a new DenialTrackingState instance (immutable update).
    """
    return DenialTrackingState(
        consecutive_denials=state.consecutive_denials + 1,
        total_denials=state.total_denials + 1,
    )


def record_success(state: DenialTrackingState) -> DenialTrackingState:
    """Record a successful permission check and reset consecutive count.

    Returns the same instance if no change needed (optimization).
    """
    if state.consecutive_denials == 0:
        return state
    return DenialTrackingState(
        consecutive_denials=0,
        total_denials=state.total_denials,
    )


def should_fallback_to_prompting(state: DenialTrackingState) -> bool:
    """Check if the circuit breaker should trigger.

    Returns True when:
    - 3 or more consecutive denials, OR
    - 20 or more total denials

    In interactive mode, this means falling back to user confirmation.
    In headless mode, this means aborting the agent.
    """
    return (
        state.consecutive_denials >= DENIAL_LIMITS["max_consecutive"]
        or state.total_denials >= DENIAL_LIMITS["max_total"]
    )


def reset_denial_state() -> DenialTrackingState:
    """Create a fresh denial tracking state."""
    return DenialTrackingState()