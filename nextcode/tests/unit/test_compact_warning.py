"""Unit tests for CompactWarningManager."""

from __future__ import annotations

from next_code.compact.warning import CompactWarningManager, WarningLevel


class TestCompactWarningManager:
    """Tests for CompactWarningManager."""

    def test_evaluate_ok_level(self):
        manager = CompactWarningManager()
        # Very low usage → ok
        warning = manager.evaluate(1_000, "claude-sonnet-4-20250514")
        assert warning.level == "ok"
        assert warning.message == ""

    def test_evaluate_warning_level(self):
        manager = CompactWarningManager()
        # Usage above warning threshold (~170K for 200K model)
        warning = manager.evaluate(170_000, "claude-sonnet-4-20250514")
        assert warning.level in ("warning", "error", "blocking")

    def test_evaluate_blocking_level(self):
        manager = CompactWarningManager()
        # Very high usage → blocking (needs to exceed effective_window - 3K ≈ 180.6K)
        warning = manager.evaluate(182_000, "claude-sonnet-4-20250514")
        assert warning.level == "blocking"
        assert "full" in warning.message.lower() or "compact" in warning.message.lower()

    def test_evaluate_percent_used(self):
        manager = CompactWarningManager()
        warning = manager.evaluate(1_000, "claude-sonnet-4-20250514")
        assert warning.percent_used >= 0
        assert warning.percent_used <= 100

    def test_suppress_after_full_compact(self):
        manager = CompactWarningManager()
        # Evaluate high usage → not ok
        warning = manager.evaluate(180_000, "claude-sonnet-4-20250514")
        assert warning.level != "ok"

        # Suppress after compact
        manager.suppress()
        assert manager.is_suppressed is True

    def test_clear_suppression_after_microcompact(self):
        manager = CompactWarningManager()
        manager.suppress()
        assert manager.is_suppressed is True

        manager.clear_suppression()
        assert manager.is_suppressed is False

    def test_warning_not_emitted_when_suppressed(self):
        manager = CompactWarningManager()
        manager.suppress()

        # Warning level is still calculated correctly
        warning = manager.evaluate(182_000, "claude-sonnet-4-20250514")
        assert warning.level == "blocking"

        # But the caller should check is_suppressed before displaying
        assert manager.is_suppressed is True

    def test_warning_emitted_when_not_suppressed(self):
        manager = CompactWarningManager()
        # Not suppressed → warnings should be shown
        assert manager.is_suppressed is False

    def test_effective_window_populated(self):
        manager = CompactWarningManager()
        warning = manager.evaluate(1_000, "claude-sonnet-4-20250514")
        assert warning.effective_window > 0
