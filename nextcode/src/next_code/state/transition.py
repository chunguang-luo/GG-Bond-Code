"""State transition tracking for the conversation loop.

Mirrors Claude Code's explicit state-transition pattern from query.ts:
each iteration of the loop records *why* it transitioned, making the
control flow debuggable without stepping through code.

Design notes:
- State is per-QueryRunner, not global — avoids cross-session pollution.
- TransitionReason enum captures every known transition cause.
- TransitionRecord logs each transition with timestamp for post-mortem analysis.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TransitionReason(Enum):
    """Reasons for state transitions in the conversation loop."""

    NEXT_TURN = "next_turn"                     # Model requested continuation
    COMPACT_RETRY = "compact_retry"             # Retrying after compaction
    SURFACE_ERROR = "surface_error"             # Exposing error to user
    STOP_HOOK_BLOCKING = "stop_hook_blocking"   # Stop hook blocked continuation
    USER_INTERRUPT = "user_interrupt"           # User pressed Ctrl+C
    TOOL_COMPLETED = "tool_completed"           # All tools completed, back to model
    MAX_TOKENS_ESCALATED = "max_tokens_escalated"  # Upgraded output limit to 64k
    MAX_TOKENS_RECOVERY = "max_tokens_recovery"    # Recovery from output limit hit
    RECOVERY_INJECTED = "recovery_injected"     # Recovery message injected into conversation
    STREAMING_DISCARD = "streaming_discard"     # Streaming executor discarded on error
    COMPACT_MICRO = "compact_micro"             # Microcompact executed
    COMPACT_FULL = "compact_full"               # Full Compact executed
    COMPACT_BLOCKING = "compact_blocking"       # Context full — blocked new query
    DONE = "done"                               # Loop ended normally


@dataclass
class TransitionRecord:
    """A single transition event."""

    reason: TransitionReason
    timestamp: float = field(default_factory=time.time)
    detail: str = ""

    def __str__(self) -> str:
        if self.detail:
            return f"[{self.reason.value}] {self.detail}"
        return f"[{self.reason.value}]"


@dataclass
class LoopState:
    """Mutable state for a single conversation loop run.

    Attached to QueryRunner and updated as the loop progresses.
    The transition log provides a full audit trail for debugging.
    """

    transition: TransitionReason | None = None
    transition_count: int = 0
    turn_count: int = 0
    user_message_count: int = 0
    tool_call_count: int = 0
    _log: list[TransitionRecord] = field(default_factory=list)

    def set_transition(self, reason: TransitionReason, detail: str = "") -> None:
        """Record a state transition."""
        self.transition = reason
        self.transition_count += 1
        self._log.append(TransitionRecord(reason=reason, detail=detail))

    @property
    def last_transition(self) -> TransitionRecord | None:
        """Return the most recent transition, or None."""
        return self._log[-1] if self._log else None

    def get_log(self) -> list[TransitionRecord]:
        """Return a copy of the transition log."""
        return list(self._log)

    def format_log(self) -> str:
        """Format the transition log as a human-readable string."""
        if not self._log:
            return "(no transitions)"
        lines = []
        for i, rec in enumerate(self._log, 1):
            ts = datetime.fromtimestamp(rec.timestamp).strftime("%Y-%m-%d %H:%M:%S")
            detail = f" — {rec.detail}" if rec.detail else ""
            lines.append(f"  {i}. [{ts}s] {rec.reason.value}{detail}")
        return "\n".join(lines)

    def reset(self) -> None:
        """Reset state for a new run."""
        self.transition = None
        self.transition_count = 0
        self.turn_count = 0
        self.user_message_count = 0
        self.tool_call_count = 0
        self._log.clear()
