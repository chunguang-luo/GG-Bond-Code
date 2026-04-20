"""Test the retry mechanism.

Tests network error retry with exponential backoff and jitter.
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock

from gg_bond_code.api.retry import (
    RetryPolicy,
    QueryType,
    is_retryable_error,
    calculate_retry_delay,
    RetryManager,
    with_retry,
)


class MockHTTPError(Exception):
    """Mock HTTP error for testing."""

    def __init__(self, status_code: int, message: str, response: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response

class MockConnectionError(Exception):
    """Mock connection error for testing."""

    pass


class TestRetryPolicy:
    """Test retry policy configuration."""

    def test_default_values(self):
        """Test default retry policy values."""
        policy = RetryPolicy()

        assert policy.MAX_RETRIES == 3
        assert policy.BASE_DELAY_MS == 500
        assert policy.MAX_DELAY_MS == 32000
        assert policy.DELAY_MULTIPLIERS == [1, 2, 4]
        assert policy.ENABLED is True


class TestIsRetryableError:
    """Test retryable error detection."""

    def test_4xx_not_retryable(self):
        """Test 4xx errors are not retryable."""
        error = MockHTTPError(status_code=404, "Not found")
        assert not is_retryable_error(error)

    def test_5xx_retryable(self):
        """Test 5xx errors are retryable."""
        error = MockHTTPError(status_code=500, "Internal Server Error")
        assert is_retryable_error(error)

    def test_connection_error_retryable(self):
        """Test connection errors are retryable."""
        error = MockConnectionError("Connection refused")
        assert is_retryable_error(error)

    def test_value_error_not_retryable(self):
        """Test ValueError is not retryable."""
        error = ValueError("Invalid input")
        assert not is_retryable_error(error)


class TestCalculateRetryDelay:
    """Test retry delay calculation."""

    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """Test exponential backoff."""
        policy = RetryPolicy()

        delay1 = await calculate_retry_delay(1, policy)
        delay2 = await calculate_retry_delay(2, policy)
        delay3 = await calculate_retry_delay(3, policy)

        # Delay should increase exponentially
        assert delay1 < delay2 < delay3

    @pytest.mark.asyncio
    async def test_max_delay_clamp(self):
        """Test that delay is clamped to max delay."""
        policy = RetryPolicy(MAX_DELAY_MS=1000)

        delay100 = await calculate_retry_delay(100, policy)
        assert delay100 <= policy.MAX_DELAY_MS

    @pytest.mark.asyncio
    async def test_jitter_within_bounds(self):
        """Test jitter is within 25% of base delay."""
        policy = RetryPolicy()

        # Run multiple times to check jitter is added
        delays = []
        for i in range(10):
            delay = await calculate_retry_delay(1, policy)
            delays.append(delay)

        base_delay = policy.BASE_DELAY_MS * 1.0  # 1 * 2^0 = 1x
        jitter_upper = base_delay * 0.25

        # All delays should be within base delay + jitter
        assert all(delay <= base_delay + jitter_upper for delay in delays)
        # And at least base delay
        assert all(delay >= base_delay for delay in delays)


class TestRetryManager:
    """Test retry manager state."""

    def test_initial_state(self):
        """Test initial state of retry manager."""
        manager = RetryManager()

        assert manager.get_retry_count("test_op") == 0
        assert manager._consecutive_5xx.get("test_op") == 0

    def test_increment_retry_count(self):
        """Test retry count increment."""
        manager = RetryManager()

        manager.increment_retry_count("test_op")
        assert manager.get_retry_count("test_op") == 1

        manager.increment_retry_count("test_op")
        manager.increment_retry_count("test_op")
        assert manager.get_retry_count("test_op") == 3

    def test_record_5xx_error(self):
        """Test 5xx error tracking."""
        manager = RetryManager()

        manager.record_5xx_error("test_op")
        assert manager._consecutive_5xx["test_op"] == 1

        manager.record_5xx_error("test_op")
        manager.record_5xx_error("test_op")
        assert manager._consecutive_5xx["test_op"] == 3

    def test_reset_5xx_counter(self):
        """Test resetting 5xx error counter."""
        manager = RetryManager()

        manager.record_5xx_error("test_op")
        assert manager._consecutive_5xx["test_op"] == 1

        manager.reset_5xx_counter("test_op")
        assert manager._consecutive_5xx["test_op"] == 0


@pytest.mark.asyncio
class TestWithRetry:
    """Test the with_retry decorator."""

    async def test_success_no_retry(self):
        """Test successful operation without retry."""
        call_count = [0]

        async def mock_operation():
            call_count[0] += 1
            return "success"

        result = await with_retry(
            operation=mock_operation,
            policy=RetryPolicy(MAX_RETRIES=3),
            query_type=QueryType.FOREGROUND,
            description="Mock operation",
        )

        assert result == "success"
        assert call_count[0] == 1  # Only called once

    async def test_5xx_error_retry(self):
        """Test retry on 5xx error."""
        call_count = [0]

        async def mock_operation():
            call_count[0] += 1
            if call_count[0] < 2:  # Fail once, succeed on second try
                raise MockHTTPError(500, "Internal Server Error")
            return "success"

        result = await with_retry(
            operation=mock_operation,
            policy=RetryPolicy(MAX_RETRIES=3),
            query_type=QueryType.FOREGROUND,
            description="Mock operation",
        )

        assert result == "success"
        assert call_count[0] == 2  # Failed once, retried once

    async def test_connection_error_retry(self):
        """Test retry on connection error."""
        call_count = [0]

        async def mock_operation():
            call_count[0] += 1
            if call_count[0] < 2:
                raise MockConnectionError("Connection refused")
            return "success"

        result = await with_retry(
            operation=mock_operation,
            policy=RetryPolicy(MAX_RETRIES=3),
            query_type=QueryType.FOREGROUND,
            description="Mock operation",
        )

        assert result == "success"
        assert call_count[0] == 2

    async def test_non_retryable_error(self):
        """Test non-retryable error raises immediately."""
        call_count = [0]

        async def mock_operation():
            call_count[0] += 1
            raise MockHTTPError(404, "Not found")

        with pytest.raises(MockHTTPError):
            await with_retry(
                operation=mock_operation,
                policy=RetryPolicy(MAX_RETRIES=3),
                query_type=QueryType.FOREGROUND,
                description="Mock operation",
            )

        assert call_count[0] == 1  # Only attempted once

    async def test_exhausted_retries(self):
        """Test exhausting retries raises last error."""
        call_count = [0]

        async def mock_operation():
            call_count[0] += 1
            raise MockHTTPError(500, "Internal Server Error")

        with pytest.raises(MockHTTPError):
            await with_retry(
                operation=mock_operation,
                policy=RetryPolicy(MAX_RETRIES=3),
                query_type=QueryType.FOREGROUND,
                description="Mock operation",
            )

        assert call_count[0] == 4  # Initial + 3 retries = 4 total attempts

    async def test_retry_with_delay(self):
        """Test retry with delay between attempts."""
        import time

        call_count = [0]
        call_times = []

        async def mock_operation():
            call_count[0] += 1
            call_times.append(time.time())
            if call_count[0] < 2:
                raise MockConnectionError("Connection refused")
            return "success"

        start_time = time.time()
        result = await with_retry(
            operation=mock_operation,
            policy=RetryPolicy(BASE_DELAY_MS=100),  # Short delay for testing
            query_type=QueryType.FOREGROUND,
            description="Mock operation",
        )

        assert result == "success"
        assert call_count[0] == 2

        # Check that there was a delay (at least 0.1s)
        elapsed = time.time() - start_time
        assert elapsed >= 0.1  # At least the base delay

        # Check delay between calls
        if len(call_times) >= 2:
            delay = call_times[1] - call_times[0]
            assert delay >= 0.1  # At least base delay

    async def test_custom_policy(self):
        """Test using custom retry policy."""
        call_count = [0]

        async def mock_operation():
            call_count[0] += 1
            if call_count[0] < 2:
                raise MockConnectionError("Connection refused")
            return "success"

        result = await with_retry(
            operation=mock_operation,
            policy=RetryPolicy(MAX_RETRIES=5, BASE_DELAY_MS=200),
            query_type=QueryType.FOREGROUND,
            description="Mock operation",
        )

        assert result == "success"
        assert call_count[0] == 2

    async def test_multiple_retries(self):
        """Test multiple retries before success."""
        call_count = [0]

        async def mock_operation():
            call_count[0] += 1
            if call_count[0] < 4:  # Fail 3 times, succeed on 4th
                raise MockConnectionError("Connection refused")
            return "success"

        result = await with_retry(
            operation=mock_operation,
            policy=RetryPolicy(MAX_RETRIES=5),
            query_type=QueryType.FOREGROUND,
            description="Mock operation",
        )

        assert result == "success"
        assert call_count[0] == 4

    async def test_operation_with_parameters(self):
        """Test retry with operation parameters."""
        called_params = []

        async def mock_operation(x: int, y: str):
            called_params.append((x, y))
            if len(called_params) < 2:
                raise MockConnectionError("Connection refused")
            return f"success: x={x}, y={y}"

        result = await with_retry(
            operation=mock_operation,
            policy=RetryPolicy(MAX_RETRIES=3),
            query_type=QueryType.FOREGROUND,
            description="Mock operation",
        )(42, "test")

        assert result == "success: x=42, y=test"
        # Parameters should be preserved across retries
        assert called_params == [(42, "test"), (42, "test")]
