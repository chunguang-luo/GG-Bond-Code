"""Prompt Cache control — cache_control marker injection for Anthropic API.

Core mechanism:
- cache_control: { type: 'ephemeral' } marker on content blocks tells the
  Anthropic API server "cache the prefix up to this point"
- Static system prompt sections get cache_control with scope=global or org
- Dynamic system prompt sections get no cache_control (scope=null)
- Last user message gets cache_control to maximize cached prefix length
- Byte-consistency: the cached prefix must match exactly across requests

Only applies to Anthropic models. OpenAI/DeepSeek use automatic caching.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class CacheScope(str, Enum):
    """Cache sharing scope for cache_control markers."""

    GLOBAL = "global"  # All users share cache
    ORG = "org"        # Organization-level sharing (default in API)
    NONE = "none"      # No cache marker


class CacheControlConfig:
    """Configuration for prompt cache behavior.

    Immutable per-session (latch pattern): once scope/ttl_1h are evaluated,
    they should not flip mid-session, or cache prefixes become inconsistent.
    """

    def __init__(
        self,
        enabled: bool = True,
        scope: CacheScope = CacheScope.ORG,
        ttl_1h: bool = False,
    ) -> None:
        self.enabled = enabled
        self._scope = scope
        self._ttl_1h = ttl_1h
        # Latch fields — set once, never flip during session
        self._latched_scope: CacheScope | None = None
        self._latched_ttl_1h: bool | None = None

    @property
    def effective_scope(self) -> CacheScope:
        """Get scope with latch protection — once set, never changes."""
        if self._latched_scope is None:
            self._latched_scope = self._scope
        return self._latched_scope

    @property
    def effective_ttl_1h(self) -> bool:
        """Get TTL with latch protection — once True, never goes back to False."""
        if self._latched_ttl_1h is None:
            self._latched_ttl_1h = self._ttl_1h
        return self._latched_ttl_1h

    def reset_latch(self) -> None:
        """Reset latches — only on /clear or /compact."""
        self._latched_scope = None
        self._latched_ttl_1h = None


def get_cache_control(
    scope: CacheScope = CacheScope.ORG,
    ttl_1h: bool = False,
) -> dict[str, Any] | None:
    """Build cache_control marker for a content block.

    Args:
        scope: Cache sharing scope.
        ttl_1h: Whether to request 1-hour TTL (requires eligibility).

    Returns:
        cache_control dict, or None if scope is NONE.
    """
    if scope == CacheScope.NONE:
        return None

    result: dict[str, Any] = {"type": "ephemeral"}
    if ttl_1h:
        result["ttl"] = "1h"
    if scope == CacheScope.GLOBAL:
        result["scope"] = "global"
    # ORG scope is default — no scope field in API request
    return result


def build_system_prompt_blocks(
    static_sections: list[str],
    dynamic_sections: list[str],
    config: CacheControlConfig,
) -> list[dict[str, Any]]:
    """Build Anthropic system prompt blocks with cache_control markers.

    Mirrors Claude Code's buildSystemPromptBlocks():
    - Static sections: cache with configured scope (global or org)
    - Dynamic sections: no cache marker (scope=null)

    Only one cache_control marker per system prompt — on the boundary
    between static and dynamic. This is because the Anthropic API's
    KV cache page manager only retains pages up to the LAST
    cache_control position.

    Args:
        static_sections: Static prompt sections (shared across users).
        dynamic_sections: Dynamic prompt sections (user-specific).
        config: Cache control configuration.

    Returns:
        List of TextBlockParam dicts for Anthropic API system parameter.
    """
    if not config.enabled:
        # No caching — merge everything into one block
        combined = "\n\n".join(static_sections + dynamic_sections)
        return [{"type": "text", "text": combined}]

    blocks: list[dict[str, Any]] = []

    # Static block with cache_control
    if static_sections:
        static_text = "\n\n".join(static_sections)
        cache_marker = get_cache_control(
            scope=config.effective_scope,
            ttl_1h=config.effective_ttl_1h,
        )
        block: dict[str, Any] = {"type": "text", "text": static_text}
        if cache_marker:
            block["cache_control"] = cache_marker
        blocks.append(block)

    # Dynamic block(s) without cache_control
    if dynamic_sections:
        dynamic_text = "\n\n".join(dynamic_sections)
        blocks.append({"type": "text", "text": dynamic_text})

    return blocks


def add_cache_breakpoint_to_messages(
    messages: list[dict[str, Any]],
    config: CacheControlConfig,
) -> list[dict[str, Any]]:
    """Add cache_control marker to the last message for message-level caching.

    Only one cache_control marker per request — on the last message.
    This maximizes the cached prefix length (system prompt + all messages
    up to and including the last one).

    Args:
        messages: Message list in Anthropic API format.
        config: Cache control configuration.

    Returns:
        New messages list with cache_control on the last message.
        Original messages are not modified.
    """
    if not config.enabled or not messages:
        return messages

    cache_marker = get_cache_control(
        scope=config.effective_scope,
        ttl_1h=config.effective_ttl_1h,
    )
    if not cache_marker:
        return messages

    # Shallow copy to avoid mutating input
    result = list(messages)

    # Add cache_control to the last message's last content block
    last_msg = result[-1]
    content = last_msg.get("content")

    if isinstance(content, list):
        # Anthropic format: content is list of blocks
        new_blocks = []
        for i, block in enumerate(content):
            if i == len(content) - 1:
                # Last block gets the marker
                new_block = {**block, "cache_control": cache_marker}
                new_blocks.append(new_block)
            else:
                new_blocks.append(block)
        result[-1] = {**last_msg, "content": new_blocks}

    elif isinstance(content, str):
        # Convert string content to block list and add marker
        result[-1] = {
            **last_msg,
            "content": [
                {"type": "text", "text": content, "cache_control": cache_marker}
            ],
        }
    else:
        # Unknown format — don't modify
        return messages

    return result
