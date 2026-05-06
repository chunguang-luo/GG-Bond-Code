"""Compact prompt engineering — NO_TOOLS_PREAMBLE and structured summary template.

The compact prompt instructs the model to produce a 9-dimension
structured summary. Two key design patterns:

1. NO_TOOLS_PREAMBLE: Compact runs with maxTurns=1. If the model
   attempts tool calls, they are rejected, wasting the API call.
   Sonnet 4.6 had a 2.79% tool-call rate without this warning.

2. CoT then strip: The model reasons in <analysis>, then produces
   the formal summary in <summary>. format_compact_summary()
   strips <analysis> to save tokens in subsequent context.
"""

from __future__ import annotations

import json
import re


NO_TOOLS_PREAMBLE = """\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.\
"""

COMPACT_PROMPT_TEMPLATE = """\
{no_tools_preamble}

Summarize the conversation so far. Use the following format:

<analysis>
Think through what has happened in the conversation:
- What was the user's primary request?
- What key technical concepts were discussed?
- What files were read or modified?
- What errors were encountered and how were they fixed?
- What is the current state of work?
</analysis>

<summary>
1. Primary Request and Intent:
2. Key Technical Concepts:
3. Files and Code Sections:
4. Errors and fixes:
5. Problem Solving:
6. All user messages:
7. Pending Tasks:
8. Current Work:
9. Optional Next Step:
</summary>\
"""


def build_compact_prompt(messages: list[dict]) -> str:
    """Build the prompt for a compact summarization request.

    Serializes the conversation history into a readable format that
    the model can summarize. Tool results are truncated to 500 chars
    to keep the prompt within reasonable size.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content")

        if isinstance(content, str):
            parts.append(f"[{role}]: {content}")
        elif isinstance(content, list):
            # Anthropic content blocks
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(f"[{role}]: {block.get('text', '')}")
                elif btype == "tool_use":
                    parts.append(
                        f"[assistant used tool {block.get('name', '')}]: "
                        f"{json.dumps(block.get('input', {}))}"
                    )
                elif btype == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, str) and len(result_content) > 500:
                        result_content = result_content[:500] + "..."
                    parts.append(
                        f"[tool result for {block.get('tool_use_id', '')}]: "
                        f"{result_content}"
                    )

        # OpenAI tool_calls
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            parts.append(
                f"[assistant used tool {func.get('name', '')}]: "
                f"{func.get('arguments', '')}"
            )
        # OpenAI tool result
        if role == "tool":
            tc_content = msg.get("content", "")
            if isinstance(tc_content, str) and len(tc_content) > 500:
                tc_content = tc_content[:500] + "..."
            parts.append(
                f"[tool result for {msg.get('tool_call_id', '')}]: "
                f"{tc_content}"
            )

    conversation_text = "\n\n".join(parts)
    return (
        COMPACT_PROMPT_TEMPLATE.format(no_tools_preamble=NO_TOOLS_PREAMBLE)
        + f"\n\n--- CONVERSATION TO SUMMARIZE ---\n\n{conversation_text}"
    )


def format_compact_summary(summary: str) -> str:
    """Format the compact summary: strip <analysis>, extract <summary>.

    This is the 'chain-of-thought then strip' technique: let the model
    reason in <analysis>, then discard it and keep only <summary>.
    This improves summary quality while saving tokens in subsequent context.
    """
    result = summary

    # Strip <analysis>...</analysis>
    result = re.sub(r"<analysis>[\s\S]*?</analysis>", "", result)

    # Extract <summary>...</summary>
    match = re.search(r"<summary>([\s\S]*?)</summary>", result)
    if match:
        result = f"Summary:\n{match.group(1).strip()}"

    return result.strip()
