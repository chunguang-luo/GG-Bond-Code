"""Compact — multi-level conversation compression."""

from .manager import CompactManager, CompactLevel
from .budget import (
    TokenWarningState,
    calculate_token_warning_state,
    estimate_token_count,
    get_effective_context_window,
    get_auto_compact_threshold,
)
from .circuit_breaker import CompactCircuitBreaker
from .micro import microcompact_messages, COMPACTABLE_TOOLS
from .full import FullCompactStrategy
from .prompt import build_compact_prompt, format_compact_summary
from .strategy import (
    CompactStrategy,
    MessageCountStrategy,
    TokenCountStrategy,
    repair_tool_references,
    should_compact_messages,
)
