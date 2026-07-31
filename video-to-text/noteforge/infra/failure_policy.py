# -*- coding: utf-8 -*-
"""
NoteForge 失败策略分类器

将异常映射为四种失败策略：
- TRANSIENT: 可重试（网络超时、429 限流）
- PERMANENT: 停止流水线（配置错误、凭证无效）
- DEGRADED: 降级继续（质量门禁失败、转写质量差）
- SKIP: 跳过当前项（视频已删除、转写太短）

注意：本模块需要跨层引用异常类型（LLMError/QualityGateFailure/ASRTimeoutError），
属于横切基础设施，不受单向依赖约束。
"""

from enum import Enum
from typing import Optional

import requests

from noteforge.core.llm_providers import LLMError
from noteforge.quality.models import QualityGateFailure
from noteforge.sources.asr_provider import ASRTimeoutError


class FailurePolicy(Enum):
    TRANSIENT = "transient"    # Retry with backoff (network timeout, 429)
    PERMANENT = "permanent"    # Stop pipeline (config error, credential invalid)
    DEGRADED = "degraded"      # Continue with annotation (poor transcription, partial concept loss)
    SKIP = "skip"              # Skip current item (video deleted, transcript too short)


# Context hint keys
_CTX_OPERATION = "operation"
_CTX_PATH = "path"

# Operations where FileNotFoundError on a file means SKIP (temp/cleanup)
_SKIP_OPERATIONS = frozenset({"cleanup", "temp", "cache"})

# Path substrings that indicate temp/ephemeral files
_TEMP_PATH_MARKERS = frozenset({"tmp", "temp", "cache", ".tmp", ".temp"})


class FailureClassifier:
    """Classify exceptions into failure policies.

    Mapping rules:
    - FileNotFoundError on temp cleanup -> SKIP
    - FileNotFoundError on config/transcript -> PERMANENT
    - UnicodeDecodeError on transcript -> PERMANENT
    - LLMError with status_code 429 -> TRANSIENT
    - LLMError with retryable=True -> TRANSIENT
    - LLMError with retryable=False -> PERMANENT
    - requests.Timeout -> TRANSIENT
    - requests.ConnectionError -> TRANSIENT
    - QualityGateFailure -> DEGRADED
    - ASRTimeoutError -> TRANSIENT
    - KeyboardInterrupt / SystemExit -> re-raise (never classify)
    - Default: PERMANENT (safe default - stop rather than continue with unknown error)
    """

    def classify(self, exception: Exception, context: Optional[dict] = None) -> FailurePolicy:
        """Classify an exception.

        Args:
            exception: The exception to classify.
            context: Optional dict with hints. Recognized keys:
                - 'operation': str — pipeline stage (download/transcribe/generate/cleanup/etc)
                - 'path': str — file path involved, used to distinguish temp vs config files

        Returns:
            FailurePolicy indicating how to handle the exception.

        Raises:
            KeyboardInterrupt, SystemExit: Re-raised without classification.
        """
        # Never classify interrupt/exit signals
        if isinstance(exception, (KeyboardInterrupt, SystemExit)):
            raise exception

        context = context or {}

        # --- FileNotFoundError: SKIP for temp/cleanup, PERMANENT otherwise ---
        if isinstance(exception, FileNotFoundError):
            if self._is_temp_context(context):
                return FailurePolicy.SKIP
            return FailurePolicy.PERMANENT

        # --- UnicodeDecodeError: always PERMANENT (bad input file) ---
        if isinstance(exception, UnicodeDecodeError):
            return FailurePolicy.PERMANENT

        # --- QualityGateFailure: DEGRADED ---
        if isinstance(exception, QualityGateFailure):
            return FailurePolicy.DEGRADED

        # --- LLMError: check status_code and retryable flag ---
        if isinstance(exception, LLMError):
            if exception.status_code == 429:
                return FailurePolicy.TRANSIENT
            if exception.retryable:
                return FailurePolicy.TRANSIENT
            return FailurePolicy.PERMANENT

        # --- ASRTimeoutError: TRANSIENT ---
        if isinstance(exception, ASRTimeoutError):
            return FailurePolicy.TRANSIENT

        # --- requests.Timeout: TRANSIENT ---
        if isinstance(exception, requests.Timeout):
            return FailurePolicy.TRANSIENT

        # --- requests.ConnectionError: TRANSIENT ---
        if isinstance(exception, requests.ConnectionError):
            return FailurePolicy.TRANSIENT

        # --- Default: PERMANENT (safe default) ---
        return FailurePolicy.PERMANENT

    def get_action(self, policy: FailurePolicy) -> str:
        """Return human-readable action description for a policy."""
        actions = {
            FailurePolicy.TRANSIENT: "Retry with exponential backoff",
            FailurePolicy.PERMANENT: "Stop pipeline and report error",
            FailurePolicy.DEGRADED: "Continue with quality annotation",
            FailurePolicy.SKIP: "Skip current item and proceed",
        }
        return actions[policy]

    def should_retry(self, policy: FailurePolicy, attempt: int, max_retries: int = 3) -> bool:
        """Whether to retry given the policy and current attempt count.

        Args:
            policy: The failure policy for the exception.
            attempt: Current attempt number (0-based).
            max_retries: Maximum number of retries allowed.

        Returns:
            True if the operation should be retried.
        """
        if policy != FailurePolicy.TRANSIENT:
            return False
        return attempt < max_retries

    def _is_temp_context(self, context: dict) -> bool:
        """Determine if the context suggests a temp/ephemeral file operation."""
        operation = context.get(_CTX_OPERATION, "")
        if operation.lower() in _SKIP_OPERATIONS:
            return True

        path = context.get(_CTX_PATH, "")
        if path:
            path_lower = path.lower().replace("\\", "/")
            for marker in _TEMP_PATH_MARKERS:
                if marker in path_lower:
                    return True

        return False
