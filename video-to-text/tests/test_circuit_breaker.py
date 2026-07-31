# -*- coding: utf-8 -*-
"""Tests for CircuitBreaker and TimeoutGuard"""

import subprocess
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

from noteforge.infra.circuit_breaker import (
    CircuitBreaker,
    TimeoutGuard,
    TIMEOUT_DOWNLOAD,
    TIMEOUT_DOWNLOAD_BATCH,
    TIMEOUT_ASR,
    TIMEOUT_LLM,
    TIMEOUT_QUALITY,
    TIMEOUT_FEISHU,
)


# ═══════════════════════════════════════════════════════════════
# CircuitBreaker tests
# ═══════════════════════════════════════════════════════════════


class TestCircuitBreakerClosedToOpen:
    """CircuitBreaker: CLOSED → OPEN on consecutive failures reaching threshold."""

    def test_default_threshold_is_3(self):
        cb = CircuitBreaker(name="test")
        assert cb._failure_threshold == 3

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.CLOSED

    def test_opens_at_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.OPEN

    def test_custom_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitBreaker.State.CLOSED
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.OPEN

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # resets counter
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.CLOSED  # only 1 failure after reset


class TestCircuitBreakerOpenToHalfOpen:
    """CircuitBreaker: OPEN → HALF_OPEN after recovery timeout."""

    def test_stays_open_before_recovery_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=10.0)
        cb.record_failure()  # opens
        assert cb.state == CircuitBreaker.State.OPEN

    def test_transitions_to_half_open_after_recovery(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()  # opens
        assert cb.state == CircuitBreaker.State.OPEN
        time.sleep(0.1)  # wait for recovery timeout
        assert cb.state == CircuitBreaker.State.HALF_OPEN

    def test_can_execute_allows_after_recovery(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()  # opens
        assert not cb.can_execute()  # still in OPEN
        time.sleep(0.1)
        assert cb.can_execute()  # now HALF_OPEN


class TestCircuitBreakerHalfOpenToClosed:
    """CircuitBreaker: HALF_OPEN → CLOSED on success."""

    def test_success_closes_circuit(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()  # opens
        time.sleep(0.1)
        assert cb.state == CircuitBreaker.State.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitBreaker.State.CLOSED

    def test_failure_count_reset_on_close(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()  # 1 failure
        cb.record_failure()  # 2 failures → OPEN
        time.sleep(0.1)
        cb.record_success()  # HALF_OPEN → CLOSED, resets counter
        assert cb._failure_count == 0
        # Now need 2 more failures to open again
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.CLOSED


class TestCircuitBreakerHalfOpenToOpen:
    """CircuitBreaker: HALF_OPEN → OPEN on failure."""

    def test_failure_reopens_from_half_open(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()  # opens
        time.sleep(0.1)
        assert cb.state == CircuitBreaker.State.HALF_OPEN
        cb.record_failure()  # reopens
        assert cb.state == CircuitBreaker.State.OPEN

    def test_half_open_max_calls(self):
        cb = CircuitBreaker(
            name="test", failure_threshold=1,
            recovery_timeout=0.05, half_open_max=2,
        )
        cb.record_failure()  # opens
        time.sleep(0.1)
        assert cb.state == CircuitBreaker.State.HALF_OPEN
        # First call allowed
        assert cb.can_execute()
        cb._increment_half_open_call()
        # Second call allowed (half_open_max=2)
        assert cb.can_execute()
        cb._increment_half_open_call()
        # Third call rejected
        assert not cb.can_execute()


class TestCircuitBreakerCanExecute:
    """CircuitBreaker: can_execute respects state."""

    def test_closed_allows_execution(self):
        cb = CircuitBreaker(name="test")
        assert cb.can_execute() is True

    def test_open_rejects_execution(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)
        cb.record_failure()
        assert cb.can_execute() is False

    def test_half_open_allows_limited_execution(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        time.sleep(0.1)
        assert cb.can_execute() is True


# ═══════════════════════════════════════════════════════════════
# TimeoutGuard tests
# ═══════════════════════════════════════════════════════════════


class TestTimeoutGuardNormalCompletion:
    """TimeoutGuard: normal completion (no timeout)."""

    def test_no_timeout_on_fast_operation(self):
        with TimeoutGuard("fast op", timeout=10) as guard:
            x = 1 + 1
        assert guard.timed_out is False

    def test_no_timeout_with_zero_timeout(self):
        with TimeoutGuard("no timeout", timeout=0) as guard:
            time.sleep(0.01)
        assert guard.timed_out is False

    def test_no_timeout_with_negative_timeout(self):
        with TimeoutGuard("no timeout", timeout=-1) as guard:
            time.sleep(0.01)
        assert guard.timed_out is False


class TestTimeoutGuardTimeoutDetection:
    """TimeoutGuard: timeout detection."""

    def test_detects_timeout(self):
        with TimeoutGuard("slow op", timeout=1) as guard:
            time.sleep(2)
        assert guard.timed_out is True

    def test_short_timeout_triggers(self):
        with TimeoutGuard("very slow", timeout=1) as guard:
            time.sleep(2)
        assert guard.timed_out is True

    def test_timer_cancelled_on_normal_exit(self):
        guard = TimeoutGuard("normal", timeout=10)
        with guard:
            pass
        # Timer should have been cancelled
        assert guard._timer is None


class TestTimeoutGuardSubprocessKill:
    """TimeoutGuard: subprocess kill on timeout."""

    def test_kills_subprocess_on_timeout(self):
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 12345
        mock_proc.kill = MagicMock()

        with TimeoutGuard("subprocess op", timeout=1) as guard:
            guard.set_process(mock_proc)
            time.sleep(2)

        assert guard.timed_out is True
        mock_proc.kill.assert_called_once()

    def test_no_kill_on_normal_completion(self):
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 12345
        mock_proc.kill = MagicMock()

        with TimeoutGuard("fast subprocess", timeout=10) as guard:
            guard.set_process(mock_proc)

        assert guard.timed_out is False
        mock_proc.kill.assert_not_called()

    def test_kill_already_exited_process(self):
        """Killing an already-exited process should not raise."""
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 12345
        mock_proc.kill.side_effect = ProcessLookupError("no such process")

        with TimeoutGuard("exited proc", timeout=1) as guard:
            guard.set_process(mock_proc)
            time.sleep(2)

        assert guard.timed_out is True
        # Should not raise despite ProcessLookupError

    def test_set_process_after_timeout(self):
        """Registering a subprocess after timeout should kill it immediately."""
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 99999
        mock_proc.kill = MagicMock()

        guard = TimeoutGuard("late register", timeout=1)
        with guard:
            time.sleep(2)
            # Process registered after timeout already fired
            guard.set_process(mock_proc)

        assert guard.timed_out is True
        mock_proc.kill.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# Timeout constants tests
# ═══════════════════════════════════════════════════════════════


class TestTimeoutConstants:
    """Timeout constants are reasonable values."""

    def test_download_timeout(self):
        assert TIMEOUT_DOWNLOAD == 1800  # 30 min

    def test_download_batch_timeout(self):
        assert TIMEOUT_DOWNLOAD_BATCH == 43200  # 12 h

    def test_asr_timeout(self):
        assert TIMEOUT_ASR == 7200  # 2 h

    def test_llm_timeout(self):
        assert TIMEOUT_LLM == 600  # 10 min

    def test_quality_timeout(self):
        assert TIMEOUT_QUALITY == 300  # 5 min

    def test_feishu_timeout(self):
        assert TIMEOUT_FEISHU == 300  # 5 min

    def test_all_timeouts_positive(self):
        for name, value in [
            ("TIMEOUT_DOWNLOAD", TIMEOUT_DOWNLOAD),
            ("TIMEOUT_DOWNLOAD_BATCH", TIMEOUT_DOWNLOAD_BATCH),
            ("TIMEOUT_ASR", TIMEOUT_ASR),
            ("TIMEOUT_LLM", TIMEOUT_LLM),
            ("TIMEOUT_QUALITY", TIMEOUT_QUALITY),
            ("TIMEOUT_FEISHU", TIMEOUT_FEISHU),
        ]:
            assert value > 0, f"{name} should be positive, got {value}"

    def test_timeout_ordering(self):
        """Batch > ASR > Download > LLM > Quality/Feishu."""
        assert TIMEOUT_DOWNLOAD_BATCH > TIMEOUT_ASR
        assert TIMEOUT_ASR > TIMEOUT_DOWNLOAD
        assert TIMEOUT_DOWNLOAD > TIMEOUT_LLM
        assert TIMEOUT_LLM > TIMEOUT_QUALITY
        assert TIMEOUT_LLM > TIMEOUT_FEISHU
