"""Tests for token budget management."""

from gg_bond_code.compact.budget import (
    MAX_OUTPUT_TOKENS_FOR_SUMMARY,
    AUTOCOMPACT_BUFFER_TOKENS,
    WARNING_THRESHOLD_BUFFER_TOKENS,
    BLOCKING_BUFFER_TOKENS,
    estimate_token_count,
    get_effective_context_window,
    get_auto_compact_threshold,
    calculate_token_warning_state,
    TokenWarningState,
)


# ── estimate_token_count ──────────────────────────────────────────────


def test_estimate_token_count_string_content():
    messages = [
        {"role": "user", "content": "Hello world"},
        {"role": "assistant", "content": "Hi there"},
    ]
    tokens = estimate_token_count(messages)
    # "Hello world" + "user" + "Hi there" + "assistant" = ~30 chars → ~7 tokens
    assert tokens > 0
    assert tokens < 100


def test_estimate_token_count_anthropic_blocks():
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me read that file."},
                {"type": "tool_use", "id": "call_1", "name": "Read", "input": {"path": "foo.py"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "file content here"},
            ],
        },
    ]
    tokens = estimate_token_count(messages)
    assert tokens > 0


def test_estimate_token_count_openai_tool_calls():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "Read", "arguments": '{"path": "foo.py"}'},
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "file content here",
        },
    ]
    tokens = estimate_token_count(messages)
    assert tokens > 0


def test_estimate_token_count_empty():
    assert estimate_token_count([]) == 0


# ── get_effective_context_window ──────────────────────────────────────


def test_get_effective_context_window_known_model():
    # claude-sonnet-4: context_window=200K, max_output=16384
    effective = get_effective_context_window("claude-sonnet-4-20250514")
    expected = 200_000 - min(16_384, MAX_OUTPUT_TOKENS_FOR_SUMMARY)
    assert effective == expected


def test_get_effective_context_window_unknown_model():
    effective = get_effective_context_window("unknown-model")
    expected = 128_000 - min(8_192, MAX_OUTPUT_TOKENS_FOR_SUMMARY)
    assert effective == expected


# ── get_auto_compact_threshold ────────────────────────────────────────


def test_get_auto_compact_threshold():
    threshold = get_auto_compact_threshold("claude-sonnet-4-20250514")
    effective = get_effective_context_window("claude-sonnet-4-20250514")
    assert threshold == effective - AUTOCOMPACT_BUFFER_TOKENS


# ── calculate_token_warning_state ─────────────────────────────────────


def test_warning_state_none():
    """Low usage returns all False."""
    state = calculate_token_warning_state(10_000, "claude-sonnet-4-20250514")
    assert isinstance(state, TokenWarningState)
    assert not state.is_above_warning
    assert not state.is_above_error
    assert not state.is_above_auto_compact
    assert not state.is_at_blocking
    assert state.percent_left > 0


def test_warning_state_blocking():
    """Extreme usage triggers blocking."""
    # For claude-sonnet-4: effective = 200K - 16384 = 183616
    # blocking = 183616 - 3000 = 180616
    effective = get_effective_context_window("claude-sonnet-4-20250514")
    blocking = effective - BLOCKING_BUFFER_TOKENS
    state = calculate_token_warning_state(blocking + 1000, "claude-sonnet-4-20250514")
    assert state.is_at_blocking
    assert state.is_above_auto_compact


def test_warning_state_auto_compact():
    """Usage at auto-compact threshold."""
    threshold = get_auto_compact_threshold("claude-sonnet-4-20250514")
    state = calculate_token_warning_state(threshold, "claude-sonnet-4-20250514")
    assert state.is_above_auto_compact


def test_warning_state_warning():
    """Usage above warning but below auto-compact."""
    threshold = get_auto_compact_threshold("claude-sonnet-4-20250514")
    warning_level = threshold - WARNING_THRESHOLD_BUFFER_TOKENS + 1000
    state = calculate_token_warning_state(warning_level, "claude-sonnet-4-20250514")
    assert state.is_above_warning
    assert not state.is_above_auto_compact


def test_warning_state_auto_compact_disabled():
    """When auto-compact is disabled, auto_compact threshold is never triggered."""
    threshold = get_auto_compact_threshold("claude-sonnet-4-20250514")
    state = calculate_token_warning_state(
        threshold, "claude-sonnet-4-20250514", auto_compact_enabled=False
    )
    assert not state.is_above_auto_compact
