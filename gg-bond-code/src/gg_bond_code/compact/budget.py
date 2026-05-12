"""Token budget management — context window sizing and warning thresholds.

Three core functions define the system's operating boundaries:
- get_effective_context_window(): actual available input space
- get_auto_compact_threshold(): token usage that triggers auto-compact
- calculate_token_warning_state(): four-level warning classification
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..api.models import (
    get_context_window_for_model,
    get_max_output_tokens_for_model,
)


# ── Budget constants ──────────────────────────────────────────────────

MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000  # Reserved for compact summary output
AUTOCOMPACT_BUFFER_TOKENS = 13_000  # Buffer before auto-compact triggers
WARNING_THRESHOLD_BUFFER_TOKENS = 20_000  # Buffer for warning level
ERROR_THRESHOLD_BUFFER_TOKENS = 20_000  # Buffer for error level
BLOCKING_BUFFER_TOKENS = 3_000  # Buffer for blocking level

# Post-compact rebuild budget
POST_COMPACT_TOKEN_BUDGET = 10_000  # Total tokens for re-injecting files after compact
MAX_TOKENS_PER_FILE = 5_000  # Max tokens per re-injected file
MAX_FILES_POST_COMPACT = 5  # Max number of files to re-inject


# ── Token estimation ─────────────────────────────────────────────────


def estimate_token_count(messages: list[dict]) -> int:
    """Estimate total token count for a message list.

    Uses the simple char/4 approximation. No tiktoken dependency.
    This is intentionally approximate — it guides compaction decisions,
    not billing or exact context limits.
    """
    total_chars = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            # Anthropic format: list of content blocks
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    total_chars += len(block.get("text", ""))
                elif btype == "tool_result":
                    total_chars += len(str(block.get("content", "")))
                elif btype == "tool_use":
                    total_chars += len(json.dumps(block.get("input", {})))

        # Count role overhead
        total_chars += len(msg.get("role", ""))

        # Count tool_calls (OpenAI format)
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            total_chars += len(func.get("arguments", ""))

    return total_chars // 4


# ── Core budget functions ─────────────────────────────────────────────


def get_effective_context_window(model: str) -> int:
    """Actual available input space = context_window - output_reserved.

    The 20K output reservation comes from p99.99 statistics:
    compact summary output max was 17,387 tokens.
    """
    context_window = get_context_window_for_model(model)
    reserved = min(
        get_max_output_tokens_for_model(model),
        MAX_OUTPUT_TOKENS_FOR_SUMMARY,
    )
    return context_window - reserved


def get_auto_compact_threshold(model: str) -> int:
    """Token usage that triggers auto-compact = effective_window - buffer.

    The 13K buffer ensures that after detecting the need for compact,
    there is still enough space for the current turn's tool calls
    and model response.
    """
    return get_effective_context_window(model) - AUTOCOMPACT_BUFFER_TOKENS


@dataclass(frozen=True)
class TokenWarningState:
    """Four-level warning state for token usage."""

    percent_left: int
    is_above_warning: bool
    is_above_error: bool
    is_above_auto_compact: bool
    is_at_blocking: bool


def calculate_token_warning_state(
    token_usage: int,
    model: str,
    auto_compact_enabled: bool = True,
) -> TokenWarningState:
    """Calculate the warning level for current token usage.

    Four levels (for a 200K model with auto-compact enabled):
    - Warning:     ~147K (threshold - 20K)  — UI shows yellow warning
    - Error:       ~147K (threshold - 20K)  — UI shows red warning
    - AutoCompact: ~167K (threshold)         — triggers auto-compact
    - Blocking:    ~177K (effective - 3K)    — blocks new queries

    Note: Warning and Error thresholds are currently identical (both 20K
    buffer). They are defined separately for future independent tuning.
    """
    auto_compact_threshold = get_auto_compact_threshold(model)
    effective_window = get_effective_context_window(model)

    threshold = auto_compact_threshold if auto_compact_enabled else effective_window

    warning_threshold = threshold - WARNING_THRESHOLD_BUFFER_TOKENS
    error_threshold = threshold - ERROR_THRESHOLD_BUFFER_TOKENS
    blocking_limit = effective_window - BLOCKING_BUFFER_TOKENS

    return TokenWarningState(
        percent_left=max(0, round(((threshold - token_usage) / threshold) * 100)),
        is_above_warning=token_usage >= warning_threshold,
        is_above_error=token_usage >= error_threshold,
        is_above_auto_compact=auto_compact_enabled
        and token_usage >= auto_compact_threshold,
        is_at_blocking=token_usage >= blocking_limit,
    )
