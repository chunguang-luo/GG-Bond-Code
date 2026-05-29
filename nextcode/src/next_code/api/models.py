"""Model metadata — context windows and output limits per model.

This module provides a model-specific lookup for context window size
and maximum output tokens. It replaces the family-based _MAX_OUTPUT_TOKENS
in client.py with per-model limits.

Lookup uses longest-prefix-match so that models with date suffixes
(e.g. claude-sonnet-4-20250514) are correctly matched.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    """Specification for a model's context capabilities."""

    context_window: int
    max_output_tokens: int


# Model metadata table: model name prefix → ModelSpec
# Prefixes are checked longest-first; first match wins.
_MODEL_SPECS: list[tuple[str, ModelSpec]] = [
    # Claude models
    ("claude-opus-4-", ModelSpec(context_window=200_000, max_output_tokens=32_768)),
    ("claude-sonnet-4-", ModelSpec(context_window=200_000, max_output_tokens=16_384)),
    ("claude-3-5-sonnet-", ModelSpec(context_window=200_000, max_output_tokens=8_192)),
    ("claude-3-5-haiku-", ModelSpec(context_window=200_000, max_output_tokens=8_192)),
    ("claude-3-opus-", ModelSpec(context_window=200_000, max_output_tokens=4_096)),
    ("claude-3-haiku-", ModelSpec(context_window=200_000, max_output_tokens=4_096)),
    # DeepSeek models
    ("deepseek-chat", ModelSpec(context_window=128_000, max_output_tokens=8_192)),
    ("deepseek-reasoner", ModelSpec(context_window=128_000, max_output_tokens=8_192)),
    ("deepseek-", ModelSpec(context_window=128_000, max_output_tokens=8_192)),
    # MiniMax models
    ("minimax-", ModelSpec(context_window=200_000, max_output_tokens=8_192)),
]

# Fallback for unknown models (200k context, matching most providers)
_DEFAULT_SPEC = ModelSpec(context_window=200_000, max_output_tokens=16_384)


def get_model_spec(model: str) -> ModelSpec:
    """Look up model spec by name. Longest prefix match wins."""
    lower = model.lower()
    best: ModelSpec | None = None
    best_len = 0
    for prefix, spec in _MODEL_SPECS:
        if lower.startswith(prefix) and len(prefix) > best_len:
            best = spec
            best_len = len(prefix)
    return best if best is not None else _DEFAULT_SPEC


def get_context_window_for_model(model: str) -> int:
    """Return the context window size for a model."""
    return get_model_spec(model).context_window


def get_max_output_tokens_for_model(model: str) -> int:
    """Return the maximum output tokens for a model."""
    return get_model_spec(model).max_output_tokens
