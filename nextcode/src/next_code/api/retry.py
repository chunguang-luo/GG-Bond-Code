"""Retry mechanism - robust network error handling with backoff and resilience.

This module provides a production-ready retry mechanism that:
- Separates retry logic from streaming
- Supports configurable retry policies
- Handles exponential backoff with jitter
- Distinguishes between different query types (foreground/background)
- Integrates with state management
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar
from enum import Enum

logger = logging.getLogger(__name__)


@dataclass
class RetryPolicy:
    """Retry policy configuration."""

    # Number of retries before giving up
    max_retries: int = 10

    # Initial and maximum delay in milliseconds
    base_delay_ms: int = 500
    max_delay_ms: int = 32000  # 32 seconds

    # Retry delay multipliers
    delay_multipliers: list[int] = field(default_factory=lambda: [1, 2, 4])

    # Whether to respect Retry-After header
    respect_retry_after: bool = True

    # Whether retry is enabled
    enabled: bool = True


class QueryType(Enum):
    """Type of query to determine retry policy."""

    FOREGROUND = "foreground"  # User-visible queries (repl_main_thread)
    BACKGROUND = "background"  # Background tasks (title generation, etc.)
    SYSTEM = "system"  # Internal system queries


T = TypeVar("T")


async def calculate_retry_delay(
    attempt: int,
    policy: RetryPolicy,
) -> int:
    """Calculate retry delay with exponential backoff and jitter.

    Args:
        attempt: Current retry attempt (1-indexed)
        policy: Retry policy configuration

    Returns:
        Delay in milliseconds
    """
    if attempt <= len(policy.delay_multipliers):
        multiplier = policy.delay_multipliers[attempt - 1]
    else:
        # Cap at max multiplier for safety
        multiplier = policy.delay_multipliers[-1]

    base_delay = policy.base_delay_ms * (2 ** (attempt - 1))

    # Add jitter to prevent thundering herd (25% of base delay)
    jitter = base_delay * random.random() * 0.25

    delay = min(base_delay + jitter, policy.max_delay_ms)

    logger.debug(
        f"Retry attempt {attempt}: "
        f"base_delay={base_delay}ms, "
        f"jitter={jitter:.0f}ms, "
        f"total_delay={delay}ms"
    )

    return delay


def is_retryable_error(error: Exception, status_code: int | None = None) -> bool:
    """Check if an error should trigger retry.

    Args:
        error: The exception that occurred
        status_code: HTTP status code from error response

    Returns:
        True if retry should be attempted, False otherwise
    """
    # Don't retry on client errors (4xx)
    if hasattr(error, "status_code"):
        if error.status_code is not None:
            # 4xx errors indicate client errors, don't retry
            if 400 <= error.status_code < 500:
                return False
            # 5xx errors are server errors, retry
            if 500 <= error.status_code < 600:
                return True
            # 6xx and above are also server errors
            return True

    # Check for other retryable error types
    # Connection errors, timeouts, rate limits, etc.
    error_name = type(error).__name__
    retryable_errors = {
        "ConnectionError",
        "TimeoutError",
        "asyncio.TimeoutError",
        "httpx.ConnectError",
        "httpx.RemoteProtocolError",
        "httpx.HTTPStatusError",
        "httpx.ReadTimeout",
        # OpenAI API errors that should be retried
        "APIConnectionError",
        "APITimeoutError",
    }

    if error_name in retryable_errors:
        return True

    # Non-retryable errors
    non_retryable_errors = {
        "ValueError",
        "TypeError",
        "json.JSONDecodeError",
        "httpx.InvalidURL",
    }

    if error_name in non_retryable_errors:
        return False

    # Fallback: if no explicit status code, assume retryable for connection errors
    if status_code is None and error_name in retryable_errors:
        return True

    return False


async def with_retry(
    operation: Callable[..., Awaitable[T]],
    *args: object,
    policy: RetryPolicy = RetryPolicy(),
    query_type: QueryType = QueryType.FOREGROUND,
    description: str = "",
    **kwargs: object,
) -> T:
    """Execute an async operation with retry logic.

    Args:
        operation: Async operation to execute with retry
        *args: Positional arguments to pass to the operation
        policy: Retry policy configuration
        query_type: Type of query (determines retry behavior)
        description: Human-readable description for logging
        **kwargs: Keyword arguments to pass to the operation

    Returns:
        Result of the operation
    """
    last_exception: Exception | None = None
    consecutive_5xx = 0

    for attempt in range(1, policy.max_retries + 1):
        try:
            logger.info(
                f"{description}: Attempt {attempt}/{policy.max_retries + 1} "
                f"for query type: {query_type.value}"
            )

            result = await operation(*args, **kwargs)

            # Success - return result
            return result

        except Exception as e:
            last_exception = e

            # Check if error is retryable
            if not is_retryable_error(e):
                logger.error(
                    f"{description}: Non-retryable error ({type(e).__name__}): {e}. "
                    f"Not retrying. Giving up after attempt {attempt}."
                )
                # Reraise to give up
                raise e

            # Log the error
            logger.warning(
                f"{description}: Error on attempt {attempt}: {type(e).__name__}: {e}"
            )

            # Handle 5xx errors specially
            if hasattr(e, "status_code"):
                if 400 <= e.status_code < 500:
                    # Client error, don't retry
                    logger.error(
                        f"{description}: Client error {e.status_code}, not retrying"
                    )
                    raise e
                elif 500 <= e.status_code < 600:
                    consecutive_5xx += 1
                    if consecutive_5xx >= 2:
                        # Too many 5xx errors, maybe server issue
                        logger.error(
                            f"{description}: Too many 5xx errors ({consecutive_5xx}), stopping"
                        )
                        raise e
            else:
                # For other retryable errors, continue
                pass

            # Calculate delay for next retry (if not last attempt)
            if attempt < policy.max_retries:
                delay_ms = await calculate_retry_delay(attempt, policy)
                await asyncio.sleep(delay_ms / 1000)

    # If we exhausted retries without success, raise last exception
    if last_exception is not None:
        logger.error(
            f"{description}: Exhausted {policy.max_retries} retries. Last error: {last_exception}"
        )
        raise last_exception

    # This should never be reached, but satisfy type checker
    raise RuntimeError("Unexpected state in retry logic")


class RetryManager:
    """Manages retry state and configuration for network operations."""

    def __init__(
        self,
        policy: RetryPolicy = RetryPolicy(),
    ) -> None:
        self._policy = policy
        self._retry_counts: dict[str, int] = {}
        self._consecutive_5xx: dict[str, int] = {}

    def get_policy(self) -> RetryPolicy:
        """Get current retry policy."""
        return self._policy

    def set_policy(self, policy: RetryPolicy) -> None:
        """Set retry policy."""
        self._policy = policy
        logger.info(f"Retry policy updated: {policy}")

    def get_retry_count(self, operation_name: str) -> int:
        """Get retry count for an operation."""
        return self._retry_counts.get(operation_name, 0)

    def increment_retry_count(self, operation_name: str) -> None:
        """Increment retry count for an operation."""
        self._retry_counts[operation_name] = self.get_retry_count(operation_name) + 1
        logger.info(f"Retry count for {operation_name}: {self._retry_counts[operation_name]}")

    def record_5xx_error(self, operation_name: str) -> None:
        """Record a 5xx error for tracking."""
        self._consecutive_5xx[operation_name] = self._consecutive_5xx.get(operation_name, 0) + 1

    def should_retry_after_delay(self, operation_name: str) -> bool:
        """Check if we should respect Retry-After header."""
        return self._policy.respect_retry_after

    def reset_5xx_counter(self, operation_name: str) -> None:
        """Reset 5xx error counter (e.g., after successful request)."""
        self._consecutive_5xx[operation_name] = 0


# Global retry manager
retry_manager = RetryManager()
