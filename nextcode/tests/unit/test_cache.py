"""Unit tests for Prompt Cache control."""

from __future__ import annotations

from next_code.api.cache import (
    CacheControlConfig,
    CacheScope,
    add_cache_breakpoint_to_messages,
    build_system_prompt_blocks,
    get_cache_control,
)


class TestGetCacheControl:
    """Tests for get_cache_control."""

    def test_none_scope_returns_none(self):
        assert get_cache_control(scope=CacheScope.NONE) is None

    def test_global_scope_includes_scope_field(self):
        result = get_cache_control(scope=CacheScope.GLOBAL)
        assert result == {"type": "ephemeral", "scope": "global"}

    def test_org_scope_no_scope_field(self):
        result = get_cache_control(scope=CacheScope.ORG)
        assert result == {"type": "ephemeral"}
        assert "scope" not in result

    def test_1h_ttl_adds_ttl_field(self):
        result = get_cache_control(scope=CacheScope.ORG, ttl_1h=True)
        assert result == {"type": "ephemeral", "ttl": "1h"}

    def test_global_with_1h_ttl(self):
        result = get_cache_control(scope=CacheScope.GLOBAL, ttl_1h=True)
        assert result == {"type": "ephemeral", "scope": "global", "ttl": "1h"}


class TestBuildSystemPromptBlocks:
    """Tests for build_system_prompt_blocks."""

    def test_no_cache_merges_everything(self):
        config = CacheControlConfig(enabled=False)
        blocks = build_system_prompt_blocks(
            static_sections=["static1", "static2"],
            dynamic_sections=["dynamic1"],
            config=config,
        )
        assert len(blocks) == 1
        assert "static1" in blocks[0]["text"]
        assert "dynamic1" in blocks[0]["text"]
        assert "cache_control" not in blocks[0]

    def test_static_cached_with_org_scope(self):
        config = CacheControlConfig(enabled=True, scope=CacheScope.ORG)
        blocks = build_system_prompt_blocks(
            static_sections=["static1"],
            dynamic_sections=["dynamic1"],
            config=config,
        )
        assert len(blocks) == 2
        # Static block has cache_control
        assert "cache_control" in blocks[0]
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        # Dynamic block has no cache_control
        assert "cache_control" not in blocks[1]

    def test_static_cached_with_global_scope(self):
        config = CacheControlConfig(enabled=True, scope=CacheScope.GLOBAL)
        blocks = build_system_prompt_blocks(
            static_sections=["static1"],
            dynamic_sections=[],
            config=config,
        )
        assert blocks[0]["cache_control"] == {"type": "ephemeral", "scope": "global"}

    def test_no_static_sections(self):
        config = CacheControlConfig(enabled=True)
        blocks = build_system_prompt_blocks(
            static_sections=[],
            dynamic_sections=["dynamic1"],
            config=config,
        )
        assert len(blocks) == 1
        assert "cache_control" not in blocks[0]

    def test_no_dynamic_sections(self):
        config = CacheControlConfig(enabled=True)
        blocks = build_system_prompt_blocks(
            static_sections=["static1"],
            dynamic_sections=[],
            config=config,
        )
        assert len(blocks) == 1
        assert "cache_control" in blocks[0]

    def test_both_empty(self):
        config = CacheControlConfig(enabled=True)
        blocks = build_system_prompt_blocks([], [], config)
        assert blocks == []


class TestAddCacheBreakpointToMessages:
    """Tests for add_cache_breakpoint_to_messages."""

    def test_empty_messages(self):
        config = CacheControlConfig(enabled=True)
        result = add_cache_breakpoint_to_messages([], config)
        assert result == []

    def test_disabled_config_returns_unchanged(self):
        config = CacheControlConfig(enabled=False)
        messages = [{"role": "user", "content": "hello"}]
        result = add_cache_breakpoint_to_messages(messages, config)
        assert result == messages

    def test_none_scope_returns_unchanged(self):
        config = CacheControlConfig(enabled=True, scope=CacheScope.NONE)
        messages = [{"role": "user", "content": "hello"}]
        result = add_cache_breakpoint_to_messages(messages, config)
        assert result == messages

    def test_string_content_gets_converted_to_blocks(self):
        config = CacheControlConfig(enabled=True)
        messages = [{"role": "user", "content": "hello"}]
        result = add_cache_breakpoint_to_messages(messages, config)
        assert len(result) == 1
        content = result[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "hello"
        assert "cache_control" in content[0]

    def test_list_content_last_block_gets_marker(self):
        config = CacheControlConfig(enabled=True)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "result"},
                    {"type": "text", "text": "hello"},
                ],
            }
        ]
        result = add_cache_breakpoint_to_messages(messages, config)
        blocks = result[0]["content"]
        # First block has no cache_control
        assert "cache_control" not in blocks[0]
        # Last block has cache_control
        assert "cache_control" in blocks[1]

    def test_original_messages_not_mutated(self):
        config = CacheControlConfig(enabled=True)
        messages = [{"role": "user", "content": "hello"}]
        original_content = messages[0]["content"]
        add_cache_breakpoint_to_messages(messages, config)
        # Original message should be unchanged
        assert messages[0]["content"] == original_content

    def test_global_scope_in_cache_marker(self):
        config = CacheControlConfig(enabled=True, scope=CacheScope.GLOBAL)
        messages = [{"role": "user", "content": "hello"}]
        result = add_cache_breakpoint_to_messages(messages, config)
        marker = result[0]["content"][0]["cache_control"]
        assert marker["scope"] == "global"


class TestCacheControlConfigLatch:
    """Tests for CacheControlConfig latch protection."""

    def test_effective_scope_latches_on_first_read(self):
        config = CacheControlConfig(scope=CacheScope.ORG)
        assert config.effective_scope == CacheScope.ORG
        # Change scope — latch should prevent flip
        config._scope = CacheScope.GLOBAL
        assert config.effective_scope == CacheScope.ORG  # Still latched

    def test_effective_ttl_latches_on_first_read(self):
        config = CacheControlConfig(ttl_1h=False)
        assert config.effective_ttl_1h is False
        # Change ttl — latch should prevent flip
        config._ttl_1h = True
        assert config.effective_ttl_1h is False  # Still latched

    def test_reset_latch_allows_new_value(self):
        config = CacheControlConfig(scope=CacheScope.ORG)
        _ = config.effective_scope  # Latch
        config._scope = CacheScope.GLOBAL
        config.reset_latch()
        assert config.effective_scope == CacheScope.GLOBAL  # New value
