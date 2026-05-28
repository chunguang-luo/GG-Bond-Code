"""Output limit recovery - recover from token limit errors."""

from abc import ABC, abstractmethod


class RecoveryStrategy(ABC):
    """Base class for recovery strategies."""

    @abstractmethod
    async def should_recover(
        self,
        error: Exception,
        recovery_count: int,
    ) -> bool:
        """Check if recovery should be attempted."""
        pass

    @abstractmethod
    async def recover(
        self,
        messages: list[dict],
        error: Exception,
    ) -> dict:
        """Perform recovery and return a message to inject."""
        pass


class MaxOutputTokensRecovery(RecoveryStrategy):
    """Recovery when max_output_tokens is hit."""

    def __init__(self, max_recovery_count: int = 3):
        self.max_recovery_count = max_recovery_count
        self.recovery_count = 0

    async def should_recover(
        self,
        error: Exception,
        recovery_count: int,
    ) -> bool:
        """Check if recovery should be attempted."""
        # Only recover from max_output_tokens errors
        error_message = str(error).lower()
        if "max_output_tokens" not in error_message and "output_tokens_exceeded" not in error_message:
            return False
        return recovery_count < self.max_recovery_count

    async def recover(
        self,
        messages: list[dict],
        error: Exception,
    ) -> dict:
        """Generate recovery message."""
        self.recovery_count += 1
        return {
            "role": "user",
            "content": "Output token limit hit. Resume directly — no apology, no recap.",
            "is_meta": True,
        }


class SurfaceErrorRecovery(RecoveryStrategy):
    """Recovery from surface API errors by exposing to user."""

    async def should_recover(
        self,
        error: Exception,
        recovery_count: int,
    ) -> bool:
        """Recover from surface errors, but NOT from client errors (4xx).

        4xx errors (401, 403, 404, etc.) indicate a fundamental problem
        (wrong URL, bad auth, wrong protocol) that retrying won't fix.
        These should be surfaced as errors, not recovered from.
        """
        # Don't recover from HTTP client errors — these are permanent
        status_code = getattr(error, "status_code", None)
        if status_code is not None and 400 <= status_code < 500:
            return False

        error_message = str(error).lower()
        return "error" in error_message and "internal" not in error_message

    async def recover(
        self,
        messages: list[dict],
        error: Exception,
    ) -> dict:
        """Expose error to user."""
        return {
            "role": "user",
            "content": f"Error: {error}",
            "is_meta": True,
        }
