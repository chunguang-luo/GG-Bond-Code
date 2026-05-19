"""Unit tests for command system caching."""

import asyncio

from next_code.commands.cache import memoize_async, clear_command_caches


class TestMemoizeAsync:
    def test_caches_result(self):
        call_count = 0

        @memoize_async
        async def load_data(key: str) -> str:
            nonlocal call_count
            call_count += 1
            return f"data-{key}"

        assert asyncio.run(load_data("a")) == "data-a"
        assert call_count == 1
        # Second call should hit cache
        assert asyncio.run(load_data("a")) == "data-a"
        assert call_count == 1

    def test_different_args_separate_cache(self):
        call_count = 0

        @memoize_async
        async def load_data(key: str) -> str:
            nonlocal call_count
            call_count += 1
            return f"data-{key}"

        asyncio.run(load_data("a"))
        asyncio.run(load_data("b"))
        assert call_count == 2

    def test_cache_clear(self):
        call_count = 0

        @memoize_async
        async def load_data(key: str) -> str:
            nonlocal call_count
            call_count += 1
            return f"data-{key}"

        asyncio.run(load_data("a"))
        assert call_count == 1

        load_data.cache_clear()

        asyncio.run(load_data("a"))
        assert call_count == 2

    def test_clear_command_caches(self):
        call_count_a = 0
        call_count_b = 0

        @memoize_async
        async def load_a(key: str) -> str:
            nonlocal call_count_a
            call_count_a += 1
            return f"a-{key}"

        @memoize_async
        async def load_b(key: str) -> str:
            nonlocal call_count_b
            call_count_b += 1
            return f"b-{key}"

        asyncio.run(load_a("x"))
        asyncio.run(load_b("y"))
        assert call_count_a == 1
        assert call_count_b == 1

        clear_command_caches()

        asyncio.run(load_a("x"))
        asyncio.run(load_b("y"))
        assert call_count_a == 2
        assert call_count_b == 2

    def test_preserves_function_name(self):
        @memoize_async
        async def my_function() -> str:
            return "result"

        assert my_function.__name__ == "my_function"

    def test_no_args(self):
        call_count = 0

        @memoize_async
        async def get_value() -> str:
            nonlocal call_count
            call_count += 1
            return "value"

        asyncio.run(get_value())
        asyncio.run(get_value())
        assert call_count == 1
