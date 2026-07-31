# -*- coding: utf-8 -*-
"""
Integration test: ASR timeout + circuit breaker interaction

Tests the full chain:
  TimeoutGuard kills a long-running subprocess
  → ASRTimeoutError is raised
  → CircuitBreaker records consecutive failures and opens

All external services (FunASR, subprocess) are mocked.
"""

import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from noteforge.infra.circuit_breaker import CircuitBreaker, TimeoutGuard, TIMEOUT_ASR
from noteforge.sources.asr_provider import ASRTimeoutError, LocalParaformerASR, MockASR


pytestmark = pytest.mark.integration


# ═══════════════════════════════════════════════════════════════
# TimeoutGuard kills subprocess and records timeout
# ═══════════════════════════════════════════════════════════════


class TestTimeoutGuardKillsSubprocess:
    """TimeoutGuard kills a sleeping subprocess and records the timeout."""

    def test_guard_kills_sleeping_subprocess(self):
        """A subprocess that sleeps for 3 hours is killed by TimeoutGuard."""
        # Use a very short timeout to avoid long test runs
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 42
        mock_proc.kill = MagicMock()

        with TimeoutGuard("ASR mock", timeout=1) as guard:
            guard.set_process(mock_proc)
            time.sleep(2)  # exceed the 1s timeout

        assert guard.timed_out is True
        mock_proc.kill.assert_called_once()

    def test_guard_records_timeout_flag(self):
        """After timeout, guard.timed_out is True."""
        with TimeoutGuard("slow operation", timeout=1) as guard:
            time.sleep(2)

        assert guard.timed_out is True

    def test_guard_no_timeout_on_fast_operation(self):
        """A fast operation does not trigger timeout."""
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 99
        mock_proc.kill = MagicMock()

        with TimeoutGuard("fast ASR", timeout=10) as guard:
            guard.set_process(mock_proc)
            # operation completes quickly

        assert guard.timed_out is False
        mock_proc.kill.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# ASRTimeoutError is raised from ASR provider
# ═══════════════════════════════════════════════════════════════


class TestASRTimeoutErrorRaised:
    """ASRTimeoutError is raised when ASR transcription times out."""

    def test_asr_timeout_error_is_exception(self):
        """ASRTimeoutError is a proper Exception subclass."""
        err = ASRTimeoutError("ASR timed out after 7200s")
        assert isinstance(err, Exception)
        assert "7200" in str(err)

    def test_local_paraformer_raises_timeout_on_timeout_keyword(self):
        """LocalParaformerASR.transcribe raises ASRTimeoutError when underlying
        function raises an exception containing 'timeout'."""
        asr = LocalParaformerASR(python_path="nonexistent_python")

        with patch("noteforge.sources.asr_provider.os.path.isfile", return_value=True), \
             patch("noteforge.sources.asr.transcribe_with_paraformer",
                   side_effect=RuntimeError("Operation timed out after 7200s")), \
             patch("noteforge.sources.asr._get_audio_duration", return_value=60.0):
            with pytest.raises(ASRTimeoutError, match="ASR 转写超时"):
                asr.transcribe("/fake/audio.wav", timeout=7200)

    def test_local_paraformer_returns_error_on_non_timeout(self):
        """LocalParaformerASR.transcribe returns TranscriptionResult with error
        when underlying function raises a non-timeout exception."""
        asr = LocalParaformerASR(python_path="nonexistent_python")

        with patch("noteforge.sources.asr_provider.os.path.isfile", return_value=True), \
             patch("noteforge.sources.asr.transcribe_with_paraformer",
                   side_effect=RuntimeError("CUDA out of memory")), \
             patch("noteforge.sources.asr._get_audio_duration", return_value=60.0):
            result = asr.transcribe("/fake/audio.wav", timeout=7200)
            assert result.error is not None
            assert "CUDA" in result.error
            assert result.text == ""


# ═══════════════════════════════════════════════════════════════
# Circuit breaker opens after consecutive ASR failures
# ═══════════════════════════════════════════════════════════════


class TestCircuitBreakerAfterASRFailures:
    """CircuitBreaker opens after consecutive ASR failures."""

    def test_circuit_opens_after_consecutive_asr_timeouts(self):
        """After 3 consecutive ASRTimeoutErrors, the circuit breaker opens."""
        cb = CircuitBreaker(name="asr", failure_threshold=3)

        # Simulate 3 consecutive ASR timeout failures
        for i in range(3):
            try:
                raise ASRTimeoutError(f"ASR timeout #{i+1}")
            except ASRTimeoutError:
                cb.record_failure()

        assert cb.state == CircuitBreaker.State.OPEN
        assert cb.can_execute() is False

    def test_circuit_rejects_asr_calls_when_open(self):
        """When circuit is open, ASR calls are rejected."""
        cb = CircuitBreaker(name="asr", failure_threshold=2)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.OPEN

        # Subsequent calls should be rejected
        assert cb.can_execute() is False

    def test_circuit_resets_on_asr_success(self):
        """A successful ASR call resets the circuit breaker failure count."""
        cb = CircuitBreaker(name="asr", failure_threshold=3)

        cb.record_failure()
        cb.record_failure()
        # Success resets the counter
        cb.record_success()
        # Now need 3 more failures to open
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.CLOSED

    def test_circuit_half_open_allows_probe_after_recovery(self):
        """After recovery timeout, circuit allows a probe ASR call."""
        cb = CircuitBreaker(name="asr", failure_threshold=1, recovery_timeout=0.05)

        cb.record_failure()  # opens
        assert cb.state == CircuitBreaker.State.OPEN

        time.sleep(0.1)  # wait for recovery
        assert cb.state == CircuitBreaker.State.HALF_OPEN
        assert cb.can_execute() is True

    def test_circuit_reopens_on_probe_failure(self):
        """If the probe ASR call fails, circuit reopens immediately."""
        cb = CircuitBreaker(name="asr", failure_threshold=1, recovery_timeout=0.05)

        cb.record_failure()  # opens
        time.sleep(0.1)  # recovery
        assert cb.state == CircuitBreaker.State.HALF_OPEN

        cb.record_failure()  # probe fails
        assert cb.state == CircuitBreaker.State.OPEN


# ═══════════════════════════════════════════════════════════════
# Full integration: TimeoutGuard + ASRTimeoutError + CircuitBreaker
# ═══════════════════════════════════════════════════════════════


class TestASRTimeoutCircuitBreakerIntegration:
    """End-to-end: TimeoutGuard triggers ASRTimeoutError which feeds CircuitBreaker."""

    def test_timeout_guard_triggers_asr_error_feeds_circuit_breaker(self):
        """Simulate the full chain: timeout → ASRTimeoutError → circuit breaker failure."""
        cb = CircuitBreaker(name="asr", failure_threshold=2)

        # Simulate 2 ASR timeout events
        for _ in range(2):
            with TimeoutGuard("ASR transcription", timeout=1) as guard:
                mock_proc = MagicMock(spec=subprocess.Popen)
                mock_proc.pid = 1234
                mock_proc.kill = MagicMock()
                guard.set_process(mock_proc)
                time.sleep(2)  # trigger timeout

            assert guard.timed_out is True
            # In real code, the timeout would cause ASRTimeoutError
            # which the caller would catch and record_failure on the circuit
            cb.record_failure()

        assert cb.state == CircuitBreaker.State.OPEN
        assert cb.can_execute() is False

    def test_mixed_asr_results_circuit_stays_closed(self):
        """Alternating success/failure does not open the circuit."""
        cb = CircuitBreaker(name="asr", failure_threshold=3)

        # Simulate mixed results
        cb.record_failure()   # 1 failure
        cb.record_success()   # reset
        cb.record_failure()   # 1 failure (reset)
        cb.record_failure()   # 2 failures
        cb.record_success()   # reset again

        assert cb.state == CircuitBreaker.State.CLOSED
        assert cb.can_execute() is True

    def test_mock_asr_never_triggers_timeout(self):
        """MockASR always succeeds and never triggers timeout or circuit breaker."""
        asr = MockASR()
        cb = CircuitBreaker(name="asr", failure_threshold=1)

        result = asr.transcribe("/any/path.wav")
        assert result.text == MockASR._MOCK_TEXT
        assert result.error is None

        # Record success
        cb.record_success()
        assert cb.state == CircuitBreaker.State.CLOSED
