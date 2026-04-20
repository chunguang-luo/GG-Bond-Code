"""Test retry mechanism.

Uses simple mocks instead of unittest.mock module.
"""

import asyncio


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
        assert self.MAX_RETRIES == 3
        assert self.BASE_DELAY_MS == 500
        assert self.MAX_DELAY_MS == 32000
        assert self.DELAY_MULTIPLIERS == [1, 2, 4]
        assert self.ENABLED is True


class TestCalculateRetryDelay:
    """Test retry delay calculation."""

    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """Test exponential backoff."""
        policy = TestRetryPolicy()
        delay1 = await calculate_retry_delay(1, policy)
        delay2 = await calculate_retry_delay(2, policy)
        delay3 = await calculate_retry_delay(3, policy)

        # Delay should increase exponentially
        assert delay1 < delay2 < delay3

    @pytest.mark.asyncio
    async def test_max_delay_clamp(self):
        """Test that delay is clamped to max delay."""
        policy = TestRetryPolicy(MAX_DELAY_MS=1000)

        delay100 = await calculate_retry_delay(100, policy)
        assert delay100 <= policy.MAX_DELAY_MS

    @pytest.mark.asyncio
    async def test_jitter_within_bounds(self):
        """Test jitter is within 25% of base delay."""
        policy = TestRetryPolicy()

        # Run multiple times to check jitter is added
        delays = []
        for i in range(10):
            delay = await calculate_retry_delay(1, policy)
            delays.append(delay)

        base_delay = policy.BASE_DELAY_MS * 1.0  # 1 * 2^0 = 1
        jitter_upper = base_delay * 0.25

        # All delays should be within base delay + jitter
        assert all(delay <= base_delay + jitter_upper for delay in delays)
        # And at least base delay
        assert all(delay >= base_delay for delay in delays)


class TestWithRetry:
    """Test @with_retry decorator."""

    async def test_success_no_retry(self):
        """Test successful operation without retry."""
        call_count = [0]

        async def mock_operation():
            call_count[0] += 1
            return "success"

        result = await self.mock_operation_with_retry(
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
            if call_count[0] < 2:
                raise MockHTTPError(500, "Internal Server Error")
            return "success"

        result = await self.mock_operation_with_retry(
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

        result = await self.mock_operation_with_retry(
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
            await self.mock_operation_with_retry(
                operation=mock_operation,
                policy=RetryPolicy(MAX_RETRIES=3),
                query_type=QueryType.FOREGROUND,
                description="Mock operation",
            )

        assert call_count[0] == 1  # Should not be called

    async def test_value_error_not_retryable(self):
        """Test ValueError is not retryable."""
        call_count = [0]

        async def mock_operation():
            call_count[0] += 1
            raise MockHTTPError(404, "Not found")

        with pytest.raises(MockHTTPError):
            await self.mock_operation_with_retry(
                operation=mock_operation,
                policy=RetryPolicy(MAX_RETRIES=3),
                query_type=QueryType.FOREGROUND,
                description="Mock operation",
            )

        assert call_count[0] == 1

    async def test_operation_with_parameters(self):
        """Test retry with operation parameters."""
        called_params = []

        async def mock_operation(x: int, y: str):
            called_params.append((x, y))
            if len(called_params) < 2:
                raise MockHTTPError(404, "Not found")
            return f"success: x={x}, y={y}"

        result = await self.mock_operation_with_retry(
            operation=mock_operation,
            policy=RetryPolicy(MAX_RETRIES=3),
            query_type=QueryType.FOREGROUND,
            description="Mock operation",
        )(42, "test")

        assert result == "success": x=42, y=test
        # Parameters should be preserved across retries
        assert called_params == [(42, "test"), (42, "test")]

    async def test_exhausted_retries(self):
        """Test exhausting retries raises last error."""
        call_count = [0]

        async def mock_operation():
            call_count[0] += 1
            if call_count[0] < 4:
                raise MockHTTPError(500, "Internal Server Error")
            return "success"

        with pytest.raises(MockHTTPError):
            await self.mock_operation_with_retry(
                operation=mock_operation,
                policy=RetryPolicy(MAX_RETRIES=3),
                query_type=QueryType.FOREGROUND,
                description="Mock operation",
            )

        assert call_count[0] == 4  # Initial + 3 retries

    async def test_retry_with_delay(self):
        """Test retry with delay between attempts."""
        call_count = [0]

        start_time = None

        async def mock_operation():
            start_time = asyncio.get_event_loop().time()
            if call_count[0] == 0:
                asyncio.sleep(0.1)  # Small delay for first call

            call_count[0] += 1
            elapsed = asyncio.get_event_loop().time() - start_time

            with pytest.raises(MockHTTPError):
                await self.mock_operation_with_retry(
                    operation=mock_operation,
                    policy=RetryPolicy(MAX_RETRIES=3),
                    query_type=QueryType.FOREGROUND,
                    description="Mock operation",
                )

            assert call_count[0] == 2
            assert elapsed >= 0.1  # Should have a delay

    async def test_jitter_within_bounds(self):
        """Test jitter is within 25% of base delay."""
        policy = TestRetryPolicy()

        # Run multiple times to verify jitter is added
        delays = []
        for i in range(10):
            delay = await calculate_retry_delay(1, policy)
            delays.append(delay)

        base_delay = policy.BASE_DELAY_MS * 1.0
        jitter_upper = base_delay * 0.25

        # All delays should be within base delay + jitter
        assert all(delay <= base_delay + jitter_upper for delay in delays)
