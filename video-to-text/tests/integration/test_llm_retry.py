# -*- coding: utf-8 -*-
"""
Integration test: LLM retry with exponential backoff + circuit breaker

Tests the full chain:
  HTTP 429 response → exponential backoff retry
  Consecutive 429s → max retries respected
  429 then 200 → eventual success
  Circuit breaker integration with LLM calls

All HTTP calls are mocked.
"""

import time
from unittest.mock import MagicMock, patch, call

import pytest
import requests

from noteforge.core.llm_providers import (
    ClaudeProvider,
    OpenAIProvider,
    LLMError,
    RetryMixin,
)
from noteforge.infra.circuit_breaker import CircuitBreaker


pytestmark = pytest.mark.integration


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _make_claude_provider(max_retries=3, base_delay=0.01, max_delay=0.1):
    """Create a ClaudeProvider with very short delays for testing."""
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        provider = ClaudeProvider({
            "model": "claude-sonnet-4-20250514",
            "base_url": "https://api.anthropic.com",
            "api_key": "test-key",
            "api_retry": {
                "max_attempts": max_retries,
                "base_delay": base_delay,
                "max_delay": max_delay,
            },
        })
    return provider


def _make_openai_provider(max_retries=3):
    """Create an OpenAIProvider with very short delays for testing."""
    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        provider = OpenAIProvider({
            "model": "gpt-4o",
            "base_url": "https://api.openai.com/v1",
            "api_key": "test-key",
        })
    # Override delays for fast tests
    provider._RETRY_BASE_DELAY = 0.01
    provider._RETRY_MAX_DELAY = 0.1
    return provider


def _mock_429_response():
    """Create a mock HTTP 429 response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 429
    resp.text = "Rate limit exceeded"
    resp.headers = {"retry-after": "1"}
    return resp


def _mock_200_claude_response(text=None):
    """Create a mock HTTP 200 response for Claude API."""
    if text is None:
        text = "这是一段足够长的生成内容，用于绕过内容安全过滤检测。" * 5
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.json.return_value = {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
    return resp


def _mock_200_openai_response(text=None):
    """Create a mock HTTP 200 response for OpenAI API."""
    if text is None:
        text = "这是一段足够长的生成内容，用于绕过内容安全过滤检测。" * 5
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }
    return resp


# ═══════════════════════════════════════════════════════════════
# Exponential backoff on 429
# ═══════════════════════════════════════════════════════════════


class TestExponentialBackoffOn429:
    """Mock HTTP 429 response, verify exponential backoff."""

    def test_single_429_then_200_succeeds(self):
        """A single 429 followed by 200 succeeds after one retry."""
        provider = _make_claude_provider(max_retries=3, base_delay=0.01, max_delay=0.1)

        responses = [_mock_429_response(), _mock_200_claude_response()]

        with patch("noteforge.core.llm_providers.requests.post",
                   side_effect=responses):
            result = provider.generate("system", "user")

        assert result and len(result) > 100  # Content safety filter bypassed

    def test_backoff_increases_exponentially(self):
        """Verify backoff delay increases exponentially across retries."""
        provider = _make_claude_provider(max_retries=3, base_delay=0.01, max_delay=0.5)

        # Track sleep calls to verify backoff
        sleep_times = []
        original_sleep = time.sleep

        def track_sleep(seconds):
            sleep_times.append(seconds)
            original_sleep(seconds)

        responses = [_mock_429_response(), _mock_429_response(), _mock_200_claude_response()]

        with patch("noteforge.core.llm_providers.requests.post",
                   side_effect=responses), \
             patch("noteforge.core.llm_providers.time.sleep", side_effect=track_sleep):
            result = provider.generate("system", "user")

        assert result and len(result) > 100  # Content safety filter bypassed
        # Should have 2 sleep calls (before retry 1 and retry 2)
        assert len(sleep_times) == 2
        # Second delay should be >= first delay (exponential)
        assert sleep_times[1] >= sleep_times[0]

    def test_openai_backoff_on_429(self):
        """OpenAI provider also retries on 429 with backoff."""
        provider = _make_openai_provider(max_retries=3)

        responses = [_mock_429_response(), _mock_200_openai_response()]

        with patch("noteforge.core.llm_providers.requests.post",
                   side_effect=responses):
            result = provider.generate("system", "user")

        assert result and len(result) > 100  # Content safety filter bypassed


# ═══════════════════════════════════════════════════════════════
# Max retries respected
# ═══════════════════════════════════════════════════════════════


class TestMaxRetriesRespected:
    """Mock consecutive 429s, verify max retries respected."""

    def test_consecutive_429s_exhaust_retries(self):
        """Consecutive 429s exhaust max retries and raise LLMError."""
        provider = _make_claude_provider(max_retries=3, base_delay=0.01, max_delay=0.05)

        # 3 consecutive 429s (all retries fail)
        responses = [_mock_429_response()] * 3

        with patch("noteforge.core.llm_providers.requests.post",
                   side_effect=responses):
            with pytest.raises(LLMError) as exc_info:
                provider.generate("system", "user")

        assert exc_info.value.status_code == 429
        assert exc_info.value.retryable is True

    def test_max_retries_configurable(self):
        """Max retries can be configured via api_retry.max_attempts."""
        provider = _make_claude_provider(max_retries=1, base_delay=0.01, max_delay=0.05)

        # Only 1 retry allowed, so 2 total attempts
        responses = [_mock_429_response(), _mock_429_response()]

        with patch("noteforge.core.llm_providers.requests.post",
                   side_effect=responses):
            with pytest.raises(LLMError) as exc_info:
                provider.generate("system", "user")

        assert exc_info.value.status_code == 429

    def test_non_retryable_status_code_raises_immediately(self):
        """Non-retryable status codes (e.g. 400) raise immediately without retry."""
        provider = _make_claude_provider(max_retries=3, base_delay=0.01, max_delay=0.05)

        resp_400 = MagicMock(spec=requests.Response)
        resp_400.status_code = 400
        resp_400.text = "Bad request: invalid model"

        with patch("noteforge.core.llm_providers.requests.post",
                   return_value=resp_400):
            with pytest.raises(LLMError) as exc_info:
                provider.generate("system", "user")

        assert exc_info.value.status_code == 400
        assert exc_info.value.retryable is False


# ═══════════════════════════════════════════════════════════════
# 429 then 200 → eventual success
# ═══════════════════════════════════════════════════════════════


class TestEventualSuccessAfter429:
    """Mock 429 then 200, verify eventual success."""

    def test_two_429s_then_200_succeeds(self):
        """Two 429s followed by 200 succeeds on the third attempt."""
        provider = _make_claude_provider(max_retries=3, base_delay=0.01, max_delay=0.1)

        responses = [
            _mock_429_response(),
            _mock_429_response(),
            _mock_200_claude_response(),
        ]

        with patch("noteforge.core.llm_providers.requests.post",
                   side_effect=responses):
            result = provider.generate("system", "user")

        assert result and len(result) > 100

    def test_openai_429_then_200_succeeds(self):
        """OpenAI: 429 then 200 succeeds."""
        provider = _make_openai_provider(max_retries=3)

        responses = [
            _mock_429_response(),
            _mock_200_openai_response(),
        ]

        with patch("noteforge.core.llm_providers.requests.post",
                   side_effect=responses):
            result = provider.generate("system", "user")

        assert result and len(result) > 100

    def test_500_then_200_succeeds(self):
        """Server error (500) is also retried and succeeds on 200."""
        provider = _make_claude_provider(max_retries=3, base_delay=0.01, max_delay=0.1)

        resp_500 = MagicMock(spec=requests.Response)
        resp_500.status_code = 500
        resp_500.text = "Internal server error"

        responses = [resp_500, _mock_200_claude_response()]

        with patch("noteforge.core.llm_providers.requests.post",
                   side_effect=responses):
            result = provider.generate("system", "user")

        assert result and len(result) > 100


# ═══════════════════════════════════════════════════════════════
# Circuit breaker integration with LLM calls
# ═══════════════════════════════════════════════════════════════


class TestCircuitBreakerWithLLM:
    """Circuit breaker integration with LLM calls."""

    def test_circuit_opens_after_consecutive_llm_failures(self):
        """Circuit breaker opens after consecutive LLM 429 failures."""
        cb = CircuitBreaker(name="llm", failure_threshold=3)
        provider = _make_claude_provider(max_retries=1, base_delay=0.01, max_delay=0.05)

        # Simulate 3 rounds of LLM failures
        for _ in range(3):
            if not cb.can_execute():
                break
            with patch("noteforge.core.llm_providers.requests.post",
                       return_value=_mock_429_response()):
                try:
                    provider.generate("system", "user")
                except LLMError:
                    cb.record_failure()

        assert cb.state == CircuitBreaker.State.OPEN
        assert cb.can_execute() is False

    def test_circuit_prevents_llm_call_when_open(self):
        """When circuit is open, LLM calls are skipped entirely."""
        cb = CircuitBreaker(name="llm", failure_threshold=1)

        # Open the circuit
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.OPEN

        # LLM call should be rejected
        assert cb.can_execute() is False

    def test_circuit_closes_on_llm_success_after_half_open(self):
        """Circuit closes after a successful LLM call in HALF_OPEN state."""
        cb = CircuitBreaker(name="llm", failure_threshold=1, recovery_timeout=0.05)
        provider = _make_claude_provider(max_retries=1, base_delay=0.01, max_delay=0.05)

        # Open the circuit
        with patch("noteforge.core.llm_providers.requests.post",
                   return_value=_mock_429_response()):
            try:
                provider.generate("system", "user")
            except LLMError:
                cb.record_failure()

        assert cb.state == CircuitBreaker.State.OPEN

        # Wait for recovery
        time.sleep(0.1)
        assert cb.state == CircuitBreaker.State.HALF_OPEN

        # Successful call closes the circuit
        with patch("noteforge.core.llm_providers.requests.post",
                   return_value=_mock_200_claude_response()):
            result = provider.generate("system", "user")
            cb.record_success()

        assert cb.state == CircuitBreaker.State.CLOSED
        assert result and len(result) > 100  # Content safety filter bypassed

    def test_circuit_reopens_on_llm_failure_in_half_open(self):
        """Circuit reopens if the probe LLM call fails in HALF_OPEN."""
        cb = CircuitBreaker(name="llm", failure_threshold=1, recovery_timeout=0.05)
        provider = _make_claude_provider(max_retries=1, base_delay=0.01, max_delay=0.05)

        # Open the circuit
        with patch("noteforge.core.llm_providers.requests.post",
                   return_value=_mock_429_response()):
            try:
                provider.generate("system", "user")
            except LLMError:
                cb.record_failure()

        # Wait for recovery
        time.sleep(0.1)
        assert cb.state == CircuitBreaker.State.HALF_OPEN

        # Probe call fails
        with patch("noteforge.core.llm_providers.requests.post",
                   return_value=_mock_429_response()):
            try:
                provider.generate("system", "user")
            except LLMError:
                cb.record_failure()

        assert cb.state == CircuitBreaker.State.OPEN

    def test_connection_error_is_retryable(self):
        """ConnectionError triggers retry in LLM provider."""
        provider = _make_claude_provider(max_retries=2, base_delay=0.01, max_delay=0.05)

        responses = [
            requests.ConnectionError("Connection refused"),
            _mock_200_claude_response(),
        ]

        with patch("noteforge.core.llm_providers.requests.post",
                   side_effect=responses):
            result = provider.generate("system", "user")

        assert result and len(result) > 100

    def test_timeout_is_retryable(self):
        """requests.Timeout triggers retry in LLM provider."""
        provider = _make_claude_provider(max_retries=2, base_delay=0.01, max_delay=0.05)

        responses = [
            requests.Timeout("Request timed out"),
            _mock_200_claude_response(),
        ]

        with patch("noteforge.core.llm_providers.requests.post",
                   side_effect=responses):
            result = provider.generate("system", "user")

        assert result and len(result) > 100
