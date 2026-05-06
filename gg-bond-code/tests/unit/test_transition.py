"""Tests for state/transition.py — LoopState and TransitionReason."""

from gg_bond_code.state.transition import LoopState, TransitionReason, TransitionRecord


# --- TransitionReason tests ---

def test_transition_reason_values():
    """All TransitionReason enum values are lowercase strings."""
    for reason in TransitionReason:
        assert reason.value == reason.value.lower()
        assert isinstance(reason.value, str)


def test_transition_reason_members():
    """TransitionReason has all expected members."""
    expected = {
        "NEXT_TURN", "COMPACT_RETRY", "SURFACE_ERROR",
        "STOP_HOOK_BLOCKING", "USER_INTERRUPT", "TOOL_COMPLETED",
        "MAX_TOKENS_ESCALATED", "MAX_TOKENS_RECOVERY",
        "RECOVERY_INJECTED", "STREAMING_DISCARD",
        "COMPACT_MICRO", "COMPACT_FULL", "COMPACT_BLOCKING",
        "DONE",
    }
    actual = {r.name for r in TransitionReason}
    assert actual == expected


# --- TransitionRecord tests ---

def test_transition_record_str_no_detail():
    """TransitionRecord.__str__ without detail shows only reason value."""
    rec = TransitionRecord(reason=TransitionReason.NEXT_TURN)
    assert str(rec) == "[next_turn]"


def test_transition_record_str_with_detail():
    """TransitionRecord.__str__ with detail includes it."""
    rec = TransitionRecord(reason=TransitionReason.COMPACT_RETRY, detail="truncated 5 msgs")
    assert "compact_retry" in str(rec)
    assert "truncated 5 msgs" in str(rec)


def test_transition_record_has_timestamp():
    """TransitionRecord has a monotonic timestamp."""
    rec = TransitionRecord(reason=TransitionReason.DONE)
    assert isinstance(rec.timestamp, float)
    assert rec.timestamp > 0


# --- LoopState tests ---

def test_loop_state_initial():
    """New LoopState has no transitions."""
    state = LoopState()
    assert state.transition is None
    assert state.transition_count == 0
    assert state.turn_count == 0
    assert state.get_log() == []
    assert state.last_transition is None


def test_set_transition():
    """set_transition updates current reason and increments count."""
    state = LoopState()
    state.set_transition(TransitionReason.NEXT_TURN, detail="run started")
    assert state.transition == TransitionReason.NEXT_TURN
    assert state.transition_count == 1


def test_set_transition_multiple():
    """Multiple transitions accumulate in the log."""
    state = LoopState()
    state.set_transition(TransitionReason.NEXT_TURN)
    state.set_transition(TransitionReason.TOOL_COMPLETED, detail="3 tools")
    state.set_transition(TransitionReason.DONE)
    assert state.transition == TransitionReason.DONE
    assert state.transition_count == 3
    assert len(state.get_log()) == 3


def test_last_transition():
    """last_transition returns the most recent record."""
    state = LoopState()
    state.set_transition(TransitionReason.NEXT_TURN)
    state.set_transition(TransitionReason.COMPACT_RETRY, detail="msg overflow")
    last = state.last_transition
    assert last is not None
    assert last.reason == TransitionReason.COMPACT_RETRY
    assert last.detail == "msg overflow"


def test_get_log_returns_copy():
    """get_log returns a copy; modifying it doesn't affect state."""
    state = LoopState()
    state.set_transition(TransitionReason.NEXT_TURN)
    log = state.get_log()
    log.clear()
    assert len(state.get_log()) == 1


def test_format_log_empty():
    """format_log on empty state shows placeholder."""
    state = LoopState()
    assert "(no transitions)" in state.format_log()


def test_format_log_with_records():
    """format_log includes reason values and details."""
    state = LoopState()
    state.set_transition(TransitionReason.NEXT_TURN, detail="run started")
    state.set_transition(TransitionReason.TOOL_COMPLETED, detail="2 tools")
    formatted = state.format_log()
    assert "next_turn" in formatted
    assert "tool_completed" in formatted
    assert "2 tools" in formatted
    # Should have numbered lines
    assert "1." in formatted
    assert "2." in formatted


def test_reset():
    """reset() clears all state."""
    state = LoopState()
    state.set_transition(TransitionReason.NEXT_TURN)
    state.turn_count = 5
    state.reset()
    assert state.transition is None
    assert state.transition_count == 0
    assert state.turn_count == 0
    assert state.get_log() == []


def test_turn_count_independent():
    """turn_count is independent of transition_count."""
    state = LoopState()
    state.turn_count = 3
    state.set_transition(TransitionReason.NEXT_TURN)
    assert state.turn_count == 3
    assert state.transition_count == 1


def test_transition_detail_empty_by_default():
    """TransitionRecord detail defaults to empty string."""
    rec = TransitionRecord(reason=TransitionReason.DONE)
    assert rec.detail == ""


def test_format_log_includes_timestamps():
    """format_log includes formatted timestamps for each record."""
    state = LoopState()
    state.set_transition(TransitionReason.NEXT_TURN)
    formatted = state.format_log()
    # Timestamp format: YYYY-MM-DD HH:MM:SS
    import re
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", formatted)
