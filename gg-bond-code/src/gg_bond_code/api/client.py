"""API client — supports both OpenAI-compatible and Anthropic backends.

Model routing:
  - deepseek-*  → OpenAI-compatible (deepseek API)
  - claude-*    → Anthropic native
  - others      → OpenAI-compatible (default)
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from gg_bond_code.config.auth import resolve_api_key

# ── Model family detection ──────────────────────────────────────────

_ANTHROPIC_PREFIXES = ("claude-",)
_OPENAI_PREFIXES = ("deepseek-",)

_DEFAULT_BASE_URLS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.deepseek.com",
}

_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "anthropic": 8192,
    "openai": 65536,
}


def _model_family(model: str) -> str:
    """Return 'anthropic' or 'openai' based on model name."""
    lower = model.lower()
    for p in _ANTHROPIC_PREFIXES:
        if lower.startswith(p):
            return "anthropic"
    for p in _OPENAI_PREFIXES:
        if lower.startswith(p):
            return "openai"
    return "openai"


# Public alias for external use
get_model_family = _model_family


# ── OpenAI-compatible client ────────────────────────────────────────

def _get_openai_client() -> Any:
    """Get or create an OpenAI-compatible async client."""
    from gg_bond_code.config.settings import get_setting

    api_key = resolve_api_key()
    base_url = get_setting("base_url") or _DEFAULT_BASE_URLS["openai"]

    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


async def _stream_openai(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str,
    model: str = "deepseek-chat",
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
                import json
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

    Uses ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL env vars automatically.
    Falls back to api_key from settings if env vars not set.
    """
    import os
    import anthropic

    from gg_bond_code.config.settings import get_setting

    api_key = os.environ.get("ANTHROPIC_API_KEY") or resolve_api_key()
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or get_setting("base_url") or _DEFAULT_BASE_URLS["anthropic"]

    return anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url)


async def _stream_anthropic(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 8192,
) -> AsyncIterator[dict[str, Any]]:
    """Stream from Anthropic API. Yields normalized events."""
    client = _get_anthropic_client()

    async with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        tools=tools,
    ) as stream:
        async for event in stream:
            if event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    yield {"type": "text_delta", "text": event.delta.text}
                elif event.delta.type == "thinking_delta":
                    yield {"type": "thinking_delta", "thinking": event.delta.thinking}
            elif event.type == "message_stop":
                break

        # Get the full message for tool use blocks (inside async with)
        message = await stream.get_final_message()
        for block in message.content:
            if block.type == "tool_use":
                yield {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }


# ── Unified entry point ─────────────────────────────────────────────

async def stream_message(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str,
    model: str = "deepseek-chat",
    max_tokens: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a message — auto-selects backend based on model name."""
    from gg_bond_code.config.settings import get_setting

    if max_tokens is None:
        max_tokens = get_setting("context.max_tokens", 65536)

    family = _model_family(model)
    # Clamp to model family's max output limit
    max_tokens = min(max_tokens, _MAX_OUTPUT_TOKENS[family])

    if family == "anthropic":
        async for evt in _stream_anthropic(messages, tools, system, model, max_tokens):
            yield evt
    else:
        async for evt in _stream_openai(messages, tools, system, model, max_tokens):
            yield evt
