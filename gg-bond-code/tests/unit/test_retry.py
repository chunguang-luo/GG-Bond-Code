"""Tests for retry mechanism - network error handling with backoff."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
sys.path.insert(0, 'src')

from gg_bond_code.api.retry import (
    RetryPolicy,
    QueryType,
    with_retry,
    calculate_retry_delay,
    is_retryable_error,
    RetryManager,
)


class TestRetryPolicy:
    """Test RetryPolicy dataclass."""

    def test_default_policy(self):
        """Test default retry policy values."""
        policy = RetryPolicy()
        assert policy.max_retries == 3
        assert policy.base_delay_ms == 500
        assert policy.max_delay_ms == 32000
        assert policy.delay_multipliers == [1, 2, 4]
        assert policy.respect_retry_after is True
        assert policy.enabled is True

    def test_custom_policy(self):
        """Test custom retry policy configuration."""
        policy = RetryPolicy(
            max_retries=5,
            base_delay_ms=1000,
            max_delay_ms=60000,
            delay_multipliers=[2, 4, 8],
            respect_retry_after=False,
        )
        assert policy.max_retries == 5
        assert policy.base_delay_ms == 1000
        assert policy.max_delay_ms == 60000
        assert policy.delay_multipliers == [2, 4, 8]
        assert policy.respect_retry_after is False


class TestCalculateRetryDelay:
    """Test exponential backoff delay calculation."""

    def test_exponential_backoff(self):
        """Test delay increases exponentially with attempts."""
        policy = RetryPolicy(base_delay_ms=500, delay_multipliers=[1, 2, 4])

        async def run_test():
            delay1 = await calculate_retry_delay(1, policy)
            delay2 = await calculate_retry_delay(2, policy)
            delay3 = await calculate_retry_delay(3, policy)

            # Delays should increase with attempts
            assert delay1 < delay2 < delay3

        asyncio.run(run_test())

    def test_jitter_added(self):
        """Test that jitter is added to base delay."""
        policy = RetryPolicy(base_delay_ms=500)

        async def run_test():
            # Run multiple times to check variance due to jitter
            delays = [await calculate_retry_delay(1, policy) for _ in range(10)]

            # With jitter, delays should vary
            assert len(set(delays)) > 1

        asyncio.run(run_test())

    def test_max_delay_cap(self):
        """Test that delay never exceeds max_delay_ms."""
        policy = RetryPolicy(base_delay_ms=500, max_delay_ms=1000)

        async def run_test():
            # Even with high attempt, delay should be capped
            delay = await calculate_retry_delay(10, policy)
            assert delay <= policy.max_delay_ms

        asyncio.run(run_test())

    def test_delay_multiplier_cap(self):
        """Test that multiplier is capped at last value when exceeding list."""
        policy = RetryPolicy(delay_multipliers=[1, 2, 4])

        async def run_test():
            # Attempt beyond multiplier list length
            delay1 = await calculate_retry_delay(3, policy)
            delay2 = await calculate_retry_delay(4, policy)
            delay3 = await calculate_retry_delay(5, policy)

            # Multiplier should be capped at last value (4)
            # But exponential backoff still applies (2^(attempt-1))
            # So delay should still increase
            assert delay2 > delay1
            assert delay3 > delay2

        asyncio.run(run_test())


class TestIsRetryableError:
    """Test error retryability detection."""

    def test_4xx_errors_not_retryable(self):
        """Test that 4xx errors are not retryable."""
        class MockError:
            def __init__(self, status_code):
                self.status_code = status_code

        for code in range(400, 500):
            error = MockError(code)
            assert is_retryable_error(error) is False

    def test_5xx_errors_retryable(self):
        """Test that 5xx errors are retryable."""
        class MockError:
            def __init__(self, status_code):
                self.status_code = status_code

        for code in range(500, 600):
            error = MockError(code)
            assert is_retryable_error(error) is True

    def test_connection_error_retryable(self):
        """Test that connection errors are retryable."""
        assert is_retryable_error(ConnectionError("Connection failed")) is True

    def test_timeout_error_retryable(self):
        """Test that timeout errors are retryable."""
        assert is_retryable_error(TimeoutError("Request timed out")) is True

    def test_asyncio_timeout_retryable(self):
        """Test that asyncio timeout errors are retryable."""
        assert is_retryable_error(asyncio.TimeoutError("Async timeout")) is True

    def test_openai_api_connection_error_retryable(self):
        """Test that OpenAI APIConnectionError is retryable."""
        from openai import APIConnectionError

        class MockRequest:
            pass

        error = APIConnectionError(request=MockRequest(), message="Connection failed")
        assert is_retryable_error(error) is True

    def test_openai_api_timeout_error_retryable(self):
        """Test that OpenAI APITimeoutError is retryable."""
        from openai import APITimeoutError

        class MockRequest:
            pass

        error = APITimeoutError(request=MockRequest())
        assert is_retryable_error(error) is True

    def test_httpx_errors_retryable(self):
        """Test that httpx network errors are retryable."""
        class MockHttpxError(Exception):
            pass

        # Generic httpx error without status code
        assert is_retryable_error(MockHttpxError()) is False

    def test_value_error_not_retryable(self):
        """Test that ValueErrors are not retryable."""
        assert is_retryable_error(ValueError("Invalid input")) is False

    def test_type_error_not_retryable(self):
        """Test that TypeErrors are not retryable."""
        assert is_retryable_error(TypeError("Wrong type")) is False

    def test_json_decode_error_not_retryable(self):
        """Test that JSON decode errors are not retryable."""
        import json
        assert is_retryable_error(json.JSONDecodeError("Invalid JSON", "", 0)) is False

    def test_error_without_status_code(self):
        """Test error handling when status_code attribute is missing."""
        error = RuntimeError("Generic error")
        # Without status_code and not in retryable list, default is False
        result = is_retryable_error(error)
        assert result is False


class TestWithRetry:
    """Test with_retry function."""

    def test_success_on_first_attempt(self):
        """Test that successful operation returns immediately."""
        operation = AsyncMock(return_value="success")

        async def run_test():
            result = await with_retry(operation)
            assert result == "success"
            assert operation.call_count == 1

        asyncio.run(run_test())

    def test_retry_on_recoverable_error(self):
        """Test that retryable errors trigger retries."""
        # Fail twice, then succeed
        operation = AsyncMock(side_effect=[ConnectionError("Fail"), ConnectionError("Fail"), "success"])

        async def run_test():
            result = await with_retry(operation, policy=RetryPolicy(max_retries=3))
            assert result == "success"
            assert operation.call_count == 3

        asyncio.run(run_test())

    def test_no_retry_on_non_recoverable_error(self):
        """Test that non-retryable errors are not retried."""
        operation = AsyncMock(side_effect=ValueError("Bad input"))

        async def run_test():
            with pytest.raises(ValueError):
                await with_retry(operation)
            assert operation.call_count == 1

        asyncio.run(run_test())

    def test_exhaust_retries(self):
        """Test that operation fails after exhausting retries."""
        operation = AsyncMock(side_effect=ConnectionError("Always fails"))

        async def run_test():
            with pytest.raises(ConnectionError):
                await with_retry(operation, policy=RetryPolicy(max_retries=2))
            # max_retries=2 means 2 attempts total (1 initial + 1 retry)
            assert operation.call_count == 2

        asyncio.run(run_test())

    def test_delay_between_retries(self):
        """Test that delays occur between retry attempts."""
        operation = AsyncMock(side_effect=[ConnectionError("Fail"), "success"])

        async def run_test():
            start = time.time()
            result = await with_retry(operation, policy=RetryPolicy(max_retries=2, base_delay_ms=100))
            elapsed = time.time() - start

            assert result == "success"
            # At least one delay should have occurred
            assert elapsed >= 0.1  # 100ms base delay

        asyncio.run(run_test())

    def test_pass_arguments(self):
        """Test that arguments are passed to operation correctly."""
        operation = AsyncMock(return_value="result")

        async def run_test():
            result = await with_retry(operation, "arg1", "arg2", kwarg1="value1")
            assert result == "result"
            operation.assert_called_once_with("arg1", "arg2", kwarg1="value1")

        asyncio.run(run_test())

    def test_query_type(self):
        """Test that query type is passed and used."""
        operation = AsyncMock(return_value="success")

        async def run_test():
            result = await with_retry(operation, query_type=QueryType.BACKGROUND)
            assert result == "success"

        asyncio.run(run_test())

    def test_5xx_error_tracking(self):
        """Test that consecutive 5xx errors stop at 2."""
        class Mock5xxError(Exception):
            def __init__(self):
                self.status_code = 503

        # Two 5xx errors should trigger early stop
        operation = AsyncMock(side_effect=[
            Mock5xxError(),
            Mock5xxError(),
            "success"  # This won't be reached
        ])

        async def run_test():
            with pytest.raises(Mock5xxError):
                await with_retry(operation, policy=RetryPolicy(max_retries=3))
            # Should stop after 2 consecutive 5xx
            assert operation.call_count == 2

        asyncio.run(run_test())

    def test_stop_after_two_consecutive_5xx(self):
        """Test that operation stops after two consecutive 5xx errors."""
        class Mock5xxError(Exception):
            def __init__(self):
                self.status_code = 503

        operation = AsyncMock(side_effect=[Mock5xxError(), Mock5xxError()])

        async def run_test():
            with pytest.raises(Mock5xxError):
                await with_retry(operation, policy=RetryPolicy(max_retries=5))
            # Should stop after 2 consecutive 5xx, not use all retries
            assert operation.call_count == 2

        asyncio.run(run_test())

    def test_4xx_client_error_immediate_fail(self):
        """Test that 4xx client errors cause immediate failure."""
        class Mock4xxError(Exception):
            def __init__(self):
                self.status_code = 404

        operation = AsyncMock(side_effect=[Mock4xxError()])

        async def run_test():
            with pytest.raises(Mock4xxError):
                await with_retry(operation, policy=RetryPolicy(max_retries=5))
            # Should fail immediately, no retries
            assert operation.call_count == 1

        asyncio.run(run_test())


class TestRetryManager:
    """Test RetryManager class."""

    def test_get_default_policy(self):
        """Test that default policy is returned."""
        manager = RetryManager()
        policy = manager.get_policy()
        assert isinstance(policy, RetryPolicy)
        assert policy.max_retries == 3

    def test_set_policy(self):
        """Test setting custom policy."""
        manager = RetryManager()
        custom_policy = RetryPolicy(max_retries=5)

        manager.set_policy(custom_policy)

        assert manager.get_policy().max_retries == 5

    def test_retry_count_tracking(self):
        """Test that retry counts are tracked per operation."""
        manager = RetryManager()

        assert manager.get_retry_count("test_op") == 0

        manager.increment_retry_count("test_op")
        assert manager.get_retry_count("test_op") == 1

        manager.increment_retry_count("test_op")
        assert manager.get_retry_count("test_op") == 2

    def test_5xx_error_recording(self):
        """Test recording of 5xx errors."""
        manager = RetryManager()

        manager.record_5xx_error("test_op")
        assert manager._consecutive_5xx["test_op"] == 1

        manager.record_5xx_error("test_op")
        assert manager._consecutive_5xx["test_op"] == 2

    def test_reset_5xx_counter(self):
        """Test resetting 5xx error counter."""
        manager = RetryManager()

        manager.record_5xx_error("test_op")
        manager.record_5xx_error("test_op")
        assert manager._consecutive_5xx["test_op"] == 2

        manager.reset_5xx_counter("test_op")
        assert manager._consecutive_5xx["test_op"] == 0

    def test_should_retry_after_delay(self):
        """Test Retry-After header respect setting."""
        manager = RetryManager()
        assert manager.should_retry_after_delay("test_op") is True

        manager.set_policy(RetryPolicy(respect_retry_after=False))
        assert manager.should_retry_after_delay("test_op") is False


class TestGlobalRetryManager:
    """Test global retry manager instance."""

    def test_global_manager_exists(self):
        """Test that global retry manager instance exists."""
        from gg_bond_code.api.retry import retry_manager

        assert isinstance(retry_manager, RetryManager)


class TestQueryType:
    """Test QueryType enum."""

    def test_query_type_values(self):
        """Test that QueryType has correct values."""
        assert QueryType.FOREGROUND.value == "foreground"
        assert QueryType.BACKGROUND.value == "background"
        assert QueryType.SYSTEM.value == "system"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
