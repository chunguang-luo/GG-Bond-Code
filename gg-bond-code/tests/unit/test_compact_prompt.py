"""Tests for compact prompt engineering."""

from gg_bond_code.compact.prompt import (
    NO_TOOLS_PREAMBLE,
    build_compact_prompt,
    format_compact_summary,
)


def test_format_compact_summary_strips_analysis():
    raw = "<analysis>Let me think about this...</analysis>\n<summary>1. Primary Request: Fix bug\n</summary>"
    result = format_compact_summary(raw)
    assert "Let me think" not in result
    assert "Primary Request" in result
    assert result.startswith("Summary:")


def test_format_compact_summary_extracts_summary():
    raw = "<summary>1. Primary Request: Fix auth bug\n2. Files: auth.py\n</summary>"
    result = format_compact_summary(raw)
    assert "Fix auth bug" in result
    assert "<summary>" not in result


def test_format_compact_summary_no_tags():
    raw = "1. Primary Request: Fix bug\n2. Files: auth.py"
    result = format_compact_summary(raw)
    assert result == raw


def test_format_compact_summary_multiple_analysis():
    raw = "<analysis>First thought</analysis> middle <analysis>Second thought</analysis> <summary>The summary</summary>"
    result = format_compact_summary(raw)
    assert "First thought" not in result
    assert "Second thought" not in result
    assert "The summary" in result


def test_build_compact_prompt_includes_no_tools_preamble():
    messages = [{"role": "user", "content": "Hello"}]
    prompt = build_compact_prompt(messages)
    assert "TEXT ONLY" in prompt
    assert "Do NOT call any tools" in prompt


def test_build_compact_prompt_serializes_string_content():
    messages = [
        {"role": "user", "content": "Fix the bug"},
        {"role": "assistant", "content": "I'll look into it"},
    ]
    prompt = build_compact_prompt(messages)
    assert "[user]: Fix the bug" in prompt
    assert "[assistant]: I'll look into it" in prompt


def test_build_compact_prompt_serializes_anthropic_blocks():
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call_1", "name": "Read", "input": {"path": "foo.py"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "file content"},
            ],
        },
    ]
    prompt = build_compact_prompt(messages)
    assert "Read" in prompt
    assert "file content" in prompt


def test_build_compact_prompt_truncates_long_results():
    long_content = "x" * 1000
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": long_content},
            ],
        },
    ]
    prompt = build_compact_prompt(messages)
    assert "x" * 1000 not in prompt  # Full content should not be there
    assert "..." in prompt  # Truncation marker should be there
