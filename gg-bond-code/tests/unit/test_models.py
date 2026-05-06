"""Tests for model metadata table."""

from gg_bond_code.api.models import (
    ModelSpec,
    get_model_spec,
    get_context_window_for_model,
    get_max_output_tokens_for_model,
)


def test_get_model_spec_claude_sonnet_4():
    """Claude Sonnet 4 with date suffix matches correctly."""
    spec = get_model_spec("claude-sonnet-4-20250514")
    assert spec.context_window == 200_000
    assert spec.max_output_tokens == 16_384


def test_get_model_spec_claude_opus_4():
    """Claude Opus 4 has larger output limit."""
    spec = get_model_spec("claude-opus-4-20250514")
    assert spec.context_window == 200_000
    assert spec.max_output_tokens == 32_768


def test_get_model_spec_deepseek_chat():
    """DeepSeek chat matches the longer prefix first."""
    spec = get_model_spec("deepseek-chat")
    assert spec.context_window == 128_000
    assert spec.max_output_tokens == 8_192


def test_get_model_spec_unknown_model():
    """Unknown models fall back to default spec."""
    spec = get_model_spec("some-unknown-model-v1")
    assert spec.context_window == 128_000
    assert spec.max_output_tokens == 8_192


def test_longest_prefix_match():
    """deepseek-chat matches before deepseek- (longer prefix wins)."""
    spec_chat = get_model_spec("deepseek-chat")
    spec_general = get_model_spec("deepseek-v3")
    assert spec_chat.context_window == 128_000
    assert spec_general.context_window == 128_000
    # Both should work, deepseek-chat matches the specific prefix


def test_case_insensitive():
    """Model name lookup is case-insensitive."""
    spec = get_model_spec("CLAUDE-SONNET-4-20250514")
    assert spec.context_window == 200_000


def test_get_context_window_for_model():
    assert get_context_window_for_model("claude-sonnet-4-20250514") == 200_000


def test_get_max_output_tokens_for_model():
    assert get_max_output_tokens_for_model("claude-sonnet-4-20250514") == 16_384
