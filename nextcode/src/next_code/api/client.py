"""API client — supports both OpenAI-compatible and Anthropic backends.

Model routing:
  - claude-*, minimax-*                    → Anthropic native
  - deepseek-, glm-, gpt-, o1-, o3-, o4-, → OpenAI-compatible
    qwen-, llama-, gemini-, mistral-, yi-  →
  - others                                 → error (unknown model)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator, Callable

import httpx

from ..config.auth import resolve_api_key

logger = logging.getLogger(__name__)

# ── Model family detection ──────────────────────────────────────────

_ANTHROPIC_PREFIXES = ("claude-", "minimax-")
_OPENAI_PREFIXES = (
    "deepseek-", "glm-", "gpt-", "o1-", "o3-", "o4-",
    "qwen-", "llama-", "gemini-", "mistral-", "yi-",
)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)

_MAX_RETRIES = 7


def _model_family(model: str) -> str | None:
    """Return 'anthropic' or 'openai' based on model name.

    Returns None if the model name doesn't match any known prefix.
    """
    lower = model.lower()
    for p in _ANTHROPIC_PREFIXES:
        if lower.startswith(p):
            return "anthropic"
    for p in _OPENAI_PREFIXES:
        if lower.startswith(p):
            return "openai"
    return None


# Public alias for external use
get_model_family = _model_family


# ── System prompt handling ────────────────────────────────────────────

from ..prompts.system import SYSTEM_PROMPT_DYNAMIC_BOUNDARY


def _split_system_prompt(
    system: str | list[str]
) -> tuple[list[str], list[str]]:
    """Split system prompt into static and dynamic parts by boundary marker.

    Args:
        system: Either a string or list of prompt sections.

    Returns:
        Tuple of (static_sections, dynamic_sections). Static sections have no
        parameters; dynamic sections require parameters like cwd.
    """
    # Convert string to list
    if isinstance(system, str):
        system_list = [system]
    else:
        system_list = list(system)

    # Find boundary marker
    boundary_idx = -1
    for i, section in enumerate(system_list):
        if section == SYSTEM_PROMPT_DYNAMIC_BOUNDARY:
            boundary_idx = i
            break

    if boundary_idx == -1:
        # No boundary, all static
        return system_list, []

    # Static: before boundary, exclude marker
    static = [s for s in system_list[:boundary_idx] if s]
    # Dynamic: after boundary, exclude marker
    dynamic = [s for s in system_list[boundary_idx+1:] if s]

    return static, dynamic


# ── Retry helper ─────────────────────────────────────────────────────

from .retry import (
    RetryPolicy,
    is_retryable_error as _is_retryable,
    calculate_retry_delay,
)


async def _retry_stream(
    gen_factory: Callable[..., AsyncIterator[dict[str, Any]]],
    *args,
    **kwargs,
) -> AsyncIterator[dict[str, Any]]:
    """Wrap an async generator factory with exponential-backoff retry.

    Note: gen_factory must return an async generator (not a coroutine).
    We iterate it with async for, which respects the async protocol.

    Args:
        gen_factory: Async generator factory (returns AsyncIterator)
        *args: Positional arguments for gen_factory
        **kwargs: Keyword arguments for gen_factory

    Yields:
        Stream events from the generator factory
    """
    from .retry import logger, RetryPolicy

    # Create a simple retry policy for streaming
    policy = RetryPolicy(max_retries=_MAX_RETRIES)

    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            # Create generator
            gen = gen_factory(*args, **kwargs)

            # Iterate and yield events
            async for evt in gen:
                yield evt

            # Success: break out of retry loop
            return

        except Exception as exc:
            last_exc = exc

            # Check if error is retryable
            if not _is_retryable(exc):
                logger.warning(f"Non-retryable error: {type(exc).__name__}: {exc}")
                raise

            # Last attempt, re-raise
            if attempt >= _MAX_RETRIES:
                raise

            # Calculate retry delay with jitter
            delay_ms = await calculate_retry_delay(attempt, policy)

            logger.warning(
                f"API error (attempt {attempt}/{_MAX_RETRIES}), "
                f"retrying in {delay_ms / 1000:.1f}s: {type(exc).__name__}: {exc}"
            )

            await asyncio.sleep(delay_ms / 1000)

    # This should not be reached, but ensure we re-raise last exception
    if last_exc is not None:
        raise last_exc  # type: ignore[misc]


# ── OpenAI-compatible client ────────────────────────────────────────

def _get_openai_client() -> Any:
    """Get or create an OpenAI-compatible async client."""
    import os
    from ..config.settings import get_setting

    api_key = resolve_api_key()
    base_url = (
        os.environ.get("NEXT_BASE_URL")
        or os.environ.get("NEXTCODE_BASE_URL")
        or get_setting("base_url")
    )

    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=_DEFAULT_TIMEOUT)


async def _stream_openai_inner(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str,
    model: str,
    max_tokens: int = 8192,
) -> AsyncIterator[dict[str, Any]]:
    """Stream from an OpenAI-compatible API. Yields normalized events."""
    client = _get_openai_client()

    # Prepend system message
    api_messages = [{"role": "system", "content": system}] + messages

    # Convert tools to OpenAI function-calling format
    openai_tools = _to_openai_tools(tools)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": api_messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if openai_tools:
        kwargs["tools"] = openai_tools

    stream = await client.chat.completions.create(**kwargs)

    # Accumulate tool calls across deltas
    tool_call_accum: dict[int, dict[str, Any]] = {}

    async for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if choice is None:
            continue

        delta = choice.delta

        # Text content
        if delta.content:
            yield {"type": "text_delta", "text": delta.content}

        # Reasoning content (DeepSeek reasoner)
        if hasattr(delta, "reasoning_content") and delta.reasoning_content:
            yield {"type": "thinking_delta", "thinking": delta.reasoning_content}

        # Tool calls
        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_call_accum:
                    tool_call_accum[idx] = {
                        "id": tc.id or "",
                        "name": "",
                        "arguments": "",
                    }
                if tc.id:
                    tool_call_accum[idx]["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        tool_call_accum[idx]["name"] = tc.function.name
                    if tc.function.arguments:
                        tool_call_accum[idx]["arguments"] += tc.function.arguments

        # Finish
        if choice.finish_reason in ("stop", "tool_calls"):
            for idx in sorted(tool_call_accum):
                tc_data = tool_call_accum[idx]
                try:
                    args = json.loads(tc_data["arguments"])
                except (json.JSONDecodeError, TypeError):
                    args = {}
                yield {
                    "type": "tool_use",
                    "id": tc_data["id"],
                    "name": tc_data["name"],
                    "input": args,
                }
            break


async def _stream_openai(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str,
    model: str,
    max_tokens: int = 8192,
) -> AsyncIterator[dict[str, Any]]:
    """Stream from OpenAI-compatible API with retry."""
    async for evt in _retry_stream(
        _stream_openai_inner, messages, tools, system, model, max_tokens
    ):
        yield evt


def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic-style tool defs to OpenAI function-calling format."""
    if not tools:
        return []
    result = []
    for t in tools:
        if "function" in t:
            result.append(t)
            continue
        func_def: dict[str, Any] = {
            "name": t["name"],
            "description": t.get("description", ""),
        }
        if "input_schema" in t:
            func_def["parameters"] = t["input_schema"]
        result.append({"type": "function", "function": func_def})
    return result


# ── Anthropic client ────────────────────────────────────────────────

def _get_anthropic_client() -> Any:
    """Get or create an Anthropic async client.

    Uses ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / NEXT_BASE_URL env vars
    automatically. Falls back to settings if env vars not set.
    """
    import os
    import anthropic

    from ..config.settings import get_setting

    api_key = os.environ.get("ANTHROPIC_API_KEY") or resolve_api_key()
    base_url = (
        os.environ.get("ANTHROPIC_BASE_URL")
        or os.environ.get("NEXT_BASE_URL")
        or get_setting("base_url")
    )

    return anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url)


async def _stream_anthropic_inner(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str | list[dict[str, Any]],
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 8192,
) -> AsyncIterator[dict[str, Any]]:
    """Stream from Anthropic API. Yields normalized events.

    Args:
        system: Either a string (legacy) or a list of TextBlockParam dicts
            with optional cache_control markers for prompt caching.
    """
    client = _get_anthropic_client()

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools

    async with client.messages.stream(**kwargs) as stream:
        async for event in stream:
            if event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    yield {"type": "text_delta", "text": event.delta.text}
                elif event.delta.type == "thinking_delta":
                    yield {"type": "thinking_delta", "thinking": event.delta.thinking}
            elif event.type == "message_stop":
                break

        # Get the full message for tool use blocks and cache stats
        message = await stream.get_final_message()

        # Emit cache statistics from API response
        if hasattr(message, "usage") and message.usage:
            yield {
                "type": "cache_stats",
                "stats": {
                    "cache_creation_input_tokens": getattr(
                        message.usage, "cache_creation_input_tokens", 0
                    ),
                    "cache_read_input_tokens": getattr(
                        message.usage, "cache_read_input_tokens", 0
                    ),
                    "input_tokens": message.usage.input_tokens,
                    "output_tokens": message.usage.output_tokens,
                },
            }

        for block in message.content:
            if block.type == "tool_use":
                yield {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }


async def _stream_anthropic(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str | list[dict[str, Any]],
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 8192,
) -> AsyncIterator[dict[str, Any]]:
    """Stream from Anthropic API with retry."""
    async for evt in _retry_stream(
        _stream_anthropic_inner, messages, tools, system, model, max_tokens
    ):
        yield evt


# ── Surrogate sanitization ───────────────────────────────────────────

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _sanitize_surrogates(obj: Any) -> Any:
    """Recursively remove UTF-16 surrogate characters from strings.

    Surrogates appear when Python decodes bytes with ``surrogateescape``
    (e.g. filenames or tool output with non-UTF-8 bytes).  They cannot be
    re-encoded to UTF-8, which causes ``UnicodeEncodeError`` when httpx
    serializes the request body to JSON.
    """
    if isinstance(obj, str):
        return _SURROGATE_RE.sub("\ufffd", obj)
    if isinstance(obj, dict):
        return {k: _sanitize_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_surrogates(v) for v in obj]
    return obj


# ── Unified entry point ─────────────────────────────────────────────

async def stream_message(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str | list[str],
    model: str,
    max_tokens: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a message — auto-selects backend based on model name.

    Args:
        messages: List of message dictionaries with 'role' and 'content'.
        tools: List of tool definitions in Anthropic format.
        system: System prompt as string or list of sections. List format
            supports static/dynamic boundary separation for prompt caching.
        model: Model name (e.g., 'deepseek-chat', 'claude-sonnet-4-20250514').
        max_tokens: Maximum output tokens. Defaults to setting or 8192.
    """
    from ..config.settings import get_setting

    if max_tokens is None:
        max_tokens = get_setting("context.max_tokens", 8192)

    family = _model_family(model)
    if family is None:
        raise ValueError(
            f"Unknown model: '{model}'. "
            f"Model name must start with a known prefix: "
            f"claude- (Anthropic), deepseek- or glm- (OpenAI-compatible). "
            f"Please configure a valid model name via --model, NEXTCODE_MODEL, "
            f"or ~/.nextcode/.settings.json (e.g. {{\"model\": \"deepseek-chat\"}})."
        )
    # Clamp to model-specific max output limit
    from .models import get_max_output_tokens_for_model

    model_max = get_max_output_tokens_for_model(model)
    max_tokens = min(max_tokens, model_max)

    # Split system prompt into static/dynamic parts
    static_sections, dynamic_sections = _split_system_prompt(system)

    # Sanitize surrogates in messages before sending to API
    messages = _sanitize_surrogates(messages)

    if family == "anthropic":
        # Build system prompt blocks with cache_control markers
        from .cache import CacheControlConfig, CacheScope, build_system_prompt_blocks, add_cache_breakpoint_to_messages

        config = CacheControlConfig(enabled=True, scope=CacheScope.ORG)
        system_blocks = build_system_prompt_blocks(static_sections, dynamic_sections, config)
        # Sanitize surrogates in system blocks
        system_blocks = _sanitize_surrogates(system_blocks)
        # Add cache breakpoint on last message
        messages = add_cache_breakpoint_to_messages(messages, config)

        async for evt in _stream_anthropic(messages, tools, system_blocks, model, max_tokens):
            yield evt
    else:
        # OpenAI-compatible: no prompt cache support, merge as string
        combined_system = "\n\n".join(static_sections + dynamic_sections)
        combined_system = _sanitize_surrogates(combined_system)
        async for evt in _stream_openai(messages, tools, combined_system, model, max_tokens):
            yield evt
