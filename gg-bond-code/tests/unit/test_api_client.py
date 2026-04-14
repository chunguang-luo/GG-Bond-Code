"""Tests for api/client.py — model routing, retry, timeout config."""

from gg_bond_code.api.client import (
    _model_family,
    get_model_family,
    _DEFAULT_TIMEOUT,
    _MAX_RETRIES,
    _RETRY_DELAYS,
    _is_retryable,
)


def test_model_family_anthropic():
    """Claude models route to anthropic."""
    assert _model_family("claude-sonnet-4-20250514") == "anthropic"
    assert _model_family("claude-opus-4-6") == "anthropic"


def test_model_family_openai():
    """DeepSeek models route to openai."""
    assert _model_family("deepseek-chat") == "openai"
    assert _model_family("deepseek-reasoner") == "openai"


def test_model_family_default_openai():
    """Unknown models default to openai."""
    assert _model_family("gpt-4") == "openai"
    assert _model_family("llama-3") == "openai"


def test_get_model_family_alias():
    """get_model_family is a public alias for _model_family."""
    assert get_model_family("claude-sonnet-4-20250514") == "anthropic"
    assert get_model_family("deepseek-chat") == "openai"


def test_default_timeout():
    """Default timeout is configured correctly."""
    assert _DEFAULT_TIMEOUT.connect == 10.0
    assert _DEFAULT_TIMEOUT.read == 120.0
    assert _DEFAULT_TIMEOUT.write == 30.0
    assert _DEFAULT_TIMEOUT.pool == 10.0


def test_retry_config():
    """Retry configuration is sensible."""
    assert _MAX_RETRIES == 3
    assert len(_RETRY_DELAYS) == 3
    assert _RETRY_DELAYS == [1.0, 2.0, 4.0]


def test_is_retryable_429():
    """429 is retryable."""
    class MockExc(Exception):
        status_code = 429
    assert _is_retryable(MockExc()) is True


def test_is_retryable_5xx():
    """5xx errors are retryable."""
    class MockExc(Exception):
        status_code = 500
    assert _is_retryable(MockExc()) is True
    class MockExc2(Exception):
        status_code = 502
    assert _is_retryable(MockExc2()) is True


def test_is_retryable_4xx():
    """4xx errors (not 429) are not retryable."""
    class MockExc(Exception):
        status_code = 400
    assert _is_retryable(MockExc()) is False
    class MockExc2(Exception):
        status_code = 403
    assert _is_retryable(MockExc2()) is False


def test_is_retryable_unknown():
    """Unknown exceptions are not retryable."""
    assert _is_retryable(RuntimeError("oops")) is False
