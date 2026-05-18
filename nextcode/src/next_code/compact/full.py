"""Full Compact — model-summarized conversation compression.

When Microcompact is not enough, Full Compact asks the model to
generate a structured 9-dimension summary of the conversation,
then replaces the full history with the summary + recent messages.

Protected by a circuit breaker: 3 consecutive failures stop further
attempts, preventing wasted API calls.
"""

from __future__ import annotations

from typing import Any

from ..api.client import stream_message
from ..api.models import get_max_output_tokens_for_model
from .budget import estimate_token_count, get_auto_compact_threshold
from .circuit_breaker import CompactCircuitBreaker
from .prompt import build_compact_prompt, format_compact_summary
from .strategy import CompactStrategy, repair_tool_references


class FullCompactStrategy(CompactStrategy):
    """Compact by asking the model to summarize the conversation.

    Uses a structured 9-dimension summary format with CoT stripping.
    Protected by a circuit breaker (3 consecutive failures → stop).
    """

    def __init__(self, max_failures: int = 3) -> None:
        self._circuit_breaker = CompactCircuitBreaker(max_failures=max_failures)

    @property
    def circuit_breaker(self) -> CompactCircuitBreaker:
        return self._circuit_breaker

    async def should_compact(
        self,
        messages: list[dict],
        model: str,
        current_tokens: int,
        max_tokens: int,
    ) -> bool:
        """Trigger when token usage exceeds the auto-compact threshold."""
        if self._circuit_breaker.is_open:
            return False
        threshold = get_auto_compact_threshold(model)
        return current_tokens >= threshold

    async def compact(
        self,
        messages: list[dict],
        model: str,
    ) -> tuple[list[dict], str]:
        """Perform Full Compact.

        1. Check circuit breaker
        2. Build compact prompt from conversation history
        3. Stream a summary from the model
        4. Format the summary (strip <analysis>, extract <summary>)
        5. Rebuild context: [summary_message] + [recent_messages]
        6. Repair tool references
        7. Record success/failure on circuit breaker
        """
        if self._circuit_breaker.is_open:
            return messages, "Circuit breaker open — skipping full compact"

        try:
            # Step 1: Build the compact prompt
            compact_prompt = build_compact_prompt(messages)

            # Step 2: Call model for summarization
            summary_text = await _collect_summary(
                prompt=compact_prompt,
                model=model,
            )

            if not summary_text.strip():
                self._circuit_breaker.record_failure()
                return messages, "Full compact produced empty summary"

            # Step 3: Format summary
            formatted = format_compact_summary(summary_text)

            # Step 4: Determine how many recent messages to keep
            keep_count = _calculate_messages_to_keep(messages)

            # Step 5: Rebuild message list
            summary_message = {
                "role": "user",
                "content": f"[Conversation summary]\n{formatted}",
            }
            recent_messages = messages[-keep_count:]
            new_messages = [summary_message] + recent_messages

            # Step 6: Repair tool references
            new_messages = repair_tool_references(new_messages)

            self._circuit_breaker.record_success()
            reason = (
                f"Full compact: summarized {len(messages)} messages, "
                f"kept {keep_count} recent"
            )
            return new_messages, reason

        except Exception as e:
            self._circuit_breaker.record_failure()
            reason = f"Full compact failed: {e}"
            return messages, reason


async def _collect_summary(
    prompt: str,
    model: str,
) -> str:
    """Stream a summary from the model and collect the full text.

    Uses stream_message with tools=[] and a special system prompt
    (the compact prompt). The conversation history is embedded in
    the prompt rather than passed as messages, keeping the API
    call simple and focused on summarization.
    """
    text_parts: list[str] = []

    # Some APIs (MiniMax, OpenAI-compatible) reject empty messages,
    # so we include a minimal user message to satisfy the requirement.
    summary_messages = [
        {"role": "user", "content": "Summarize the conversation above."},
    ]

    async for evt in stream_message(
        messages=summary_messages,
        tools=[],  # NO tools — critical!
        system=prompt,  # Compact prompt as system message
        model=model,
        max_tokens=min(get_max_output_tokens_for_model(model), 20_000),
    ):
        if evt.get("type") == "text_delta":
            text_parts.append(evt.get("text", ""))

    return "".join(text_parts)


def _calculate_messages_to_keep(
    messages: list[dict],
    min_messages: int = 5,
    max_messages: int = 20,
) -> int:
    """Determine how many recent messages to keep after full compact.

    Mirrors Claude Code's calculateMessagesToKeepIndex:
    - Minimum 5 messages with text
    - Maximum 20 messages
    - Ensure tool_use/tool_result pairs are not split
    """
    count = min(max_messages, len(messages))
    count = max(min_messages, count)

    # Adjust to not split tool_use/tool_result pairs
    if count < len(messages):
        for i in range(count, 0, -1):
            msg = messages[len(messages) - i]
            role = msg.get("role", "")
            # Safe to start here if it's a user message without tool results
            if role == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    count = i
                    break
                if isinstance(content, list) and all(
                    not isinstance(b, dict) or b.get("type") != "tool_result"
                    for b in content
                ):
                    count = i
                    break

    return count
