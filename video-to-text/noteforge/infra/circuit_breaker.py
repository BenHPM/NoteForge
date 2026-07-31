# -*- coding: utf-8 -*-
"""
NoteForge 断路器 + 超时守卫

CircuitBreaker: LLM/ASR 调用的断路器保护
  CLOSED (正常) → OPEN (失败累积，拒绝调用) → HALF_OPEN (试探恢复)

TimeoutGuard: 操作超时守卫（Windows 兼容，使用 threading.Timer）
  支持子进程自动终止（ASR 等长时间子进程操作）
"""

import logging
import subprocess
import threading
from enum import Enum
from time import monotonic
from typing import Optional

logger = logging.getLogger('noteforge.infra.circuit_breaker')


# ── 超时常量 ──

TIMEOUT_DOWNLOAD = 1800          # 30 min — 单个视频/音频下载
TIMEOUT_DOWNLOAD_BATCH = 43200   # 12 h   — 批量下载（无人值守）
TIMEOUT_ASR = 7200               # 2 h    — ASR 转写（长音频）
TIMEOUT_LLM = 600                # 10 min — LLM 单次调用
TIMEOUT_QUALITY = 300            # 5 min  — 质量门禁评估
TIMEOUT_FEISHU = 300             # 5 min  — 飞书 API 调用


class CircuitBreaker:
    """Circuit breaker for LLM/ASR calls.

    States: CLOSED (normal) → OPEN (failing, reject calls) → HALF_OPEN (testing recovery)

    Config:
    - failure_threshold: consecutive failures before opening (default 3)
    - recovery_timeout: seconds before trying half-open (default 60)
    - half_open_max: calls allowed in half-open state (default 1)
    """

    class State(Enum):
        CLOSED = "closed"
        OPEN = "open"
        HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
        half_open_max: int = 1,
    ):
        """Initialize circuit breaker.

        Args:
            name: Identifier for logging (e.g. 'llm', 'asr').
            failure_threshold: Consecutive failures before opening circuit.
            recovery_timeout: Seconds in OPEN state before transitioning to HALF_OPEN.
            half_open_max: Number of calls allowed in HALF_OPEN state per window.
        """
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max

        self._state = self.State.CLOSED
        self._failure_count = 0
        self._half_open_calls = 0
        self._last_failure_time: Optional[float] = None

    # ── Public API ──

    def can_execute(self) -> bool:
        """Whether a new call should be allowed.

        Returns:
            True if the circuit allows execution, False if it should be rejected.
        """
        self._check_recovery()

        if self._state == self.State.CLOSED:
            return True

        if self._state == self.State.HALF_OPEN:
            if self._half_open_calls < self._half_open_max:
                return True
            return False

        # OPEN state
        return False

    def _check_recovery(self) -> None:
        """Lazily transition OPEN → HALF_OPEN if recovery timeout has elapsed."""
        if self._state == self.State.OPEN and self._last_failure_time is not None:
            elapsed = monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout:
                self._transition_to(self.State.HALF_OPEN)

    def record_success(self) -> None:
        """Record a successful call.

        In CLOSED state: resets failure counter.
        In HALF_OPEN state: transitions back to CLOSED.
        In OPEN state: checks recovery timeout first, then handles accordingly.
        """
        self._check_recovery()

        if self._state == self.State.HALF_OPEN:
            logger.info("Circuit [%s] HALF_OPEN → CLOSED (success)", self._name)
            self._state = self.State.CLOSED
            self._failure_count = 0
            self._half_open_calls = 0
        elif self._state == self.State.CLOSED:
            self._failure_count = 0
        else:
            # OPEN state — recovery timeout not yet elapsed; should not receive success
            self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call.

        In CLOSED state: increments failure counter, opens if threshold reached.
        In HALF_OPEN state: reopens the circuit immediately.
        In OPEN state: updates last failure time.
        """
        self._check_recovery()
        self._last_failure_time = monotonic()

        if self._state == self.State.HALF_OPEN:
            logger.warning("Circuit [%s] HALF_OPEN → OPEN (failure during probe)", self._name)
            self._state = self.State.OPEN
            self._half_open_calls = 0
            return

        if self._state == self.State.CLOSED:
            self._failure_count += 1
            if self._failure_count >= self._failure_threshold:
                logger.warning(
                    "Circuit [%s] CLOSED → OPEN (%d consecutive failures)",
                    self._name, self._failure_count,
                )
                self._state = self.State.OPEN
            return

        # Already OPEN — just update the timestamp (already done above)

    @property
    def state(self) -> 'CircuitBreaker.State':
        """Current circuit state.

        Lazily transitions OPEN → HALF_OPEN if recovery timeout has elapsed,
        so callers see the effective state.
        """
        self._check_recovery()
        return self._state

    # ── Internal ──

    def _transition_to(self, new_state: 'CircuitBreaker.State') -> None:
        """Transition to a new state with appropriate bookkeeping."""
        old = self._state
        self._state = new_state

        if new_state == self.State.HALF_OPEN:
            self._half_open_calls = 0
            logger.info("Circuit [%s] %s → HALF_OPEN (recovery timeout elapsed)", self._name, old.value)
        elif new_state == self.State.CLOSED:
            self._failure_count = 0
            self._half_open_calls = 0
        elif new_state == self.State.OPEN:
            self._half_open_calls = 0

    def _increment_half_open_call(self) -> None:
        """Track a call in HALF_OPEN state (called internally by can_execute)."""
        if self._state == self.State.HALF_OPEN:
            self._half_open_calls += 1


class TimeoutGuard:
    """Context manager for operation timeouts.

    Usage:
        with TimeoutGuard("ASR transcription", timeout=7200) as guard:
            result = do_something()
        if guard.timed_out:
            handle_timeout()

    On Windows, uses threading.Timer (no SIGALRM).
    On Unix, could use signal.alarm for more precise control.

    For subprocess-based operations (ASR), provides a method to kill the subprocess:
        with TimeoutGuard("ASR", timeout=7200) as guard:
            proc = subprocess.Popen(...)
            guard.set_process(proc)  # register for auto-kill on timeout
            ...
    """

    def __init__(self, operation: str, timeout: int):
        """Initialize timeout guard.

        Args:
            operation: Human-readable operation name (for logging/error messages).
            timeout: Timeout in seconds. 0 or negative means no timeout.
        """
        self._operation = operation
        self._timeout = timeout
        self._timed_out = False
        self._timer: Optional[threading.Timer] = None
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def __enter__(self) -> 'TimeoutGuard':
        if self._timeout > 0:
            self._timer = threading.Timer(self._timeout, self._on_timeout)
            self._timer.daemon = True
            self._timer.start()
            logger.debug("TimeoutGuard [%s]: started (%ds)", self._operation, self._timeout)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

        if self._timed_out:
            logger.warning("TimeoutGuard [%s]: timed out after %ds", self._operation, self._timeout)
            # Suppress any exception from the with-block; the timeout is the real issue
            return True  # swallow exceptions if we timed out

        logger.debug("TimeoutGuard [%s]: completed normally", self._operation)
        return False

    def set_process(self, proc: subprocess.Popen) -> None:
        """Register a subprocess for auto-kill on timeout.

        Args:
            proc: The subprocess to terminate if the operation times out.
        """
        with self._lock:
            self._process = proc
            # If already timed out before registration, kill immediately
            if self._timed_out:
                self._kill_process()

    @property
    def timed_out(self) -> bool:
        """Whether the operation timed out."""
        return self._timed_out

    # ── Internal ──

    def _on_timeout(self) -> None:
        """Timer callback: mark as timed out and kill any registered subprocess."""
        with self._lock:
            self._timed_out = True
            self._kill_process()
        logger.error(
            "TimeoutGuard [%s]: operation timed out after %ds",
            self._operation, self._timeout,
        )

    def _kill_process(self) -> None:
        """Kill the registered subprocess if it exists. Must be called under lock."""
        if self._process is not None:
            try:
                self._process.kill()
                logger.warning(
                    "TimeoutGuard [%s]: killed subprocess (PID %s)",
                    self._operation, self._process.pid,
                )
            except (OSError, ProcessLookupError):
                # Process already exited — fine
                pass
