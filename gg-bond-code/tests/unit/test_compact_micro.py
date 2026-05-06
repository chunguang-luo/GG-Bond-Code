"""Tests for microcompact — lightweight tool result cleanup."""

from gg_bond_code.compact.micro import (
    COMPACTABLE_TOOLS,
    microcompact_messages,
    TIME_BASED_MC_CLEARED_MESSAGE,
)


def test_microcompact_no_compactable_tools():
    """Messages without tool results pass through unchanged."""
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]
    result = microcompact_messages(messages)
    assert result.messages == messages
    assert result.cleared_count == 0


def test_microcompact_anthropic_tool_results():
    """Anthropic format: old tool results are replaced with placeholder."""
    messages = [
        {"role": "user", "content": "Read the file"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call_1", "name": "Read", "input": {"path": "foo.py"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "x" * 1000},
            ],
        },
        {"role": "assistant", "content": "I see the issue."},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call_2", "name": "Read", "input": {"path": "bar.py"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_2", "content": "y" * 1000},
            ],
        },
        {"role": "user", "content": "Next question"},
    ]
    result = microcompact_messages(messages, keep_recent=1)
    assert result.cleared_count == 1
    # The old tool result should be replaced
    user_msg = result.messages[2]
    tool_result = user_msg["content"][0]
    assert tool_result["content"] == TIME_BASED_MC_CLEARED_MESSAGE
    assert result.estimated_tokens_freed > 0


def test_microcompact_openai_tool_results():
    """OpenAI format: old tool messages are replaced with placeholder."""
    messages = [
        {"role": "user", "content": "Read the file"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "x" * 1000},
        {"role": "assistant", "content": "Let me check another file."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_2", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_2", "content": "y" * 1000},
        {"role": "user", "content": "Next question"},
    ]
    result = microcompact_messages(messages, keep_recent=1)
    assert result.cleared_count == 1
    # The tool message should have placeholder content
    tool_msg = result.messages[2]
    assert tool_msg["content"] == TIME_BASED_MC_CLEARED_MESSAGE


def test_microcompact_keep_recent():
    """Only the last N tool results are kept."""
    messages = []
    for i in range(5):
        messages.append({
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": f"call_{i}", "name": "Read", "input": {}},
            ],
        })
        messages.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": f"call_{i}", "content": f"result {i}"},
            ],
        })
    result = microcompact_messages(messages, keep_recent=2)
    assert result.cleared_count == 3  # 5 total - 2 kept


def test_microcompact_mixed_tools():
    """Only compactable tools are cleared; non-compactable are preserved."""
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call_read_1", "name": "Read", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_read_1", "content": "old file content"},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call_agent", "name": "Agent", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_agent", "content": "agent result"},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call_read_2", "name": "Read", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_read_2", "content": "new file content"},
            ],
        },
    ]
    result = microcompact_messages(messages, keep_recent=1)
    # Read is compactable (2 results), Agent is not (1 result, not in COMPACTABLE_TOOLS)
    # With keep_recent=1: 2 compactable results, keep 1, clear 1
    assert result.cleared_count == 1


def test_microcompact_preserves_non_tool_content():
    """Text blocks in the same message as tool results are preserved."""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Some text"},
                {"type": "tool_result", "tool_use_id": "call_1", "content": "result"},
            ],
        },
    ]
    # Need the corresponding tool_use for the result to be found as compactable
    messages.insert(
        0,
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call_1", "name": "Read", "input": {}},
            ],
        },
    )
    result = microcompact_messages(messages, keep_recent=0)
    # The text block should still be there
    user_msg = result.messages[1]
    text_blocks = [b for b in user_msg["content"] if b.get("type") == "text"]
    assert len(text_blocks) == 1
    assert text_blocks[0]["text"] == "Some text"


def test_microcompact_empty_messages():
    """Empty message list returns empty result."""
    result = microcompact_messages([])
    assert result.messages == []
    assert result.cleared_count == 0


def test_microcompact_all_recent():
    """When all results are within keep_recent, nothing is cleared."""
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call_1", "name": "Read", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "result"},
            ],
        },
    ]
    result = microcompact_messages(messages, keep_recent=5)
    assert result.cleared_count == 0
    assert result.messages == messages
