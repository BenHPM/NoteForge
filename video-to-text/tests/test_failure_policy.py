# -*- coding: utf-8 -*-
"""
FailurePolicy / FailureClassifier 单元测试

覆盖：
- 各异常类型映射到正确的策略
- 上下文提示影响分类（FileNotFoundError on temp vs config）
- should_retry 逻辑
- KeyboardInterrupt 不被分类（re-raise）
- 默认分类
- get_action 返回有意义的字符串
"""

import pytest
import requests

from noteforge.infra.failure_policy import FailurePolicy, FailureClassifier
from noteforge.core.llm_providers import LLMError
from noteforge.quality.models import QualityGateFailure
from noteforge.sources.asr_provider import ASRTimeoutError


@pytest.fixture
def classifier():
    return FailureClassifier()


# ---- 1. FileNotFoundError on config/transcript -> PERMANENT ----

class TestFileNotFoundErrorPermanent:
    def test_fnf_no_context_is_permanent(self, classifier):
        exc = FileNotFoundError("config.yaml not found")
        assert classifier.classify(exc) == FailurePolicy.PERMANENT

    def test_fnf_generate_operation_is_permanent(self, classifier):
        exc = FileNotFoundError("transcript.txt not found")
        assert classifier.classify(exc, {"operation": "generate"}) == FailurePolicy.PERMANENT

    def test_fnf_config_path_is_permanent(self, classifier):
        exc = FileNotFoundError("not found")
        assert classifier.classify(exc, {"path": "config/llm_engine_config.yaml"}) == FailurePolicy.PERMANENT


# ---- 2. FileNotFoundError on temp/cleanup -> SKIP ----

class TestFileNotFoundErrorSkip:
    def test_fnf_cleanup_operation_is_skip(self, classifier):
        exc = FileNotFoundError("temp file already deleted")
        assert classifier.classify(exc, {"operation": "cleanup"}) == FailurePolicy.SKIP

    def test_fnf_temp_operation_is_skip(self, classifier):
        exc = FileNotFoundError("temp file missing")
        assert classifier.classify(exc, {"operation": "temp"}) == FailurePolicy.SKIP

    def test_fnf_cache_operation_is_skip(self, classifier):
        exc = FileNotFoundError("cache file missing")
        assert classifier.classify(exc, {"operation": "cache"}) == FailurePolicy.SKIP

    def test_fnf_tmp_path_is_skip(self, classifier):
        exc = FileNotFoundError("not found")
        assert classifier.classify(exc, {"path": "/tmp/audio_segment.wav"}) == FailurePolicy.SKIP

    def test_fnf_temp_in_path_is_skip(self, classifier):
        exc = FileNotFoundError("not found")
        assert classifier.classify(exc, {"path": "output/temp/segment.wav"}) == FailurePolicy.SKIP

    def test_fnf_cache_in_path_is_skip(self, classifier):
        exc = FileNotFoundError("not found")
        assert classifier.classify(exc, {"path": "output/cache/hash_cache.json"}) == FailurePolicy.SKIP


# ---- 3. UnicodeDecodeError -> PERMANENT ----

class TestUnicodeDecodeError:
    def test_unicode_decode_error_is_permanent(self, classifier):
        exc = UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 2, "invalid start byte")
        assert classifier.classify(exc) == FailurePolicy.PERMANENT


# ---- 4. LLMError mapping ----

class TestLLMErrorMapping:
    def test_llm_error_429_is_transient(self, classifier):
        exc = LLMError("Rate limited", status_code=429, retryable=True)
        assert classifier.classify(exc) == FailurePolicy.TRANSIENT

    def test_llm_error_429_not_retryable_flag_still_transient(self, classifier):
        """429 status code overrides retryable flag — rate limit is always transient."""
        exc = LLMError("Rate limited", status_code=429, retryable=False)
        assert classifier.classify(exc) == FailurePolicy.TRANSIENT

    def test_llm_error_retryable_is_transient(self, classifier):
        exc = LLMError("Temporary failure", status_code=503, retryable=True)
        assert classifier.classify(exc) == FailurePolicy.TRANSIENT

    def test_llm_error_not_retryable_is_permanent(self, classifier):
        exc = LLMError("Invalid API key", status_code=401, retryable=False)
        assert classifier.classify(exc) == FailurePolicy.PERMANENT

    def test_llm_error_default_not_retryable_is_permanent(self, classifier):
        exc = LLMError("Unknown error")
        assert classifier.classify(exc) == FailurePolicy.PERMANENT


# ---- 5. requests exceptions -> TRANSIENT ----

class TestRequestsExceptions:
    def test_requests_timeout_is_transient(self, classifier):
        exc = requests.Timeout("Connection timed out")
        assert classifier.classify(exc) == FailurePolicy.TRANSIENT

    def test_requests_connection_error_is_transient(self, classifier):
        exc = requests.ConnectionError("Connection refused")
        assert classifier.classify(exc) == FailurePolicy.TRANSIENT


# ---- 6. QualityGateFailure -> DEGRADED ----

class TestQualityGateFailure:
    def test_quality_gate_failure_is_degraded(self, classifier):
        exc = QualityGateFailure("Quality score 0.45 below threshold 0.60")
        assert classifier.classify(exc) == FailurePolicy.DEGRADED


# ---- 7. ASRTimeoutError -> TRANSIENT ----

class TestASRTimeoutError:
    def test_asr_timeout_is_transient(self, classifier):
        exc = ASRTimeoutError("ASR transcription timed out after 600s")
        assert classifier.classify(exc) == FailurePolicy.TRANSIENT


# ---- 8. KeyboardInterrupt / SystemExit -> re-raise ----

class TestInterruptReraise:
    def test_keyboard_interrupt_is_reraised(self, classifier):
        with pytest.raises(KeyboardInterrupt):
            classifier.classify(KeyboardInterrupt())

    def test_system_exit_is_reraised(self, classifier):
        with pytest.raises(SystemExit):
            classifier.classify(SystemExit(1))


# ---- 9. Default classification -> PERMANENT ----

class TestDefaultClassification:
    def test_unknown_exception_is_permanent(self, classifier):
        exc = RuntimeError("Something unexpected")
        assert classifier.classify(exc) == FailurePolicy.PERMANENT

    def test_value_error_is_permanent(self, classifier):
        exc = ValueError("Bad value")
        assert classifier.classify(exc) == FailurePolicy.PERMANENT

    def test_type_error_is_permanent(self, classifier):
        exc = TypeError("Wrong type")
        assert classifier.classify(exc) == FailurePolicy.PERMANENT


# ---- 10. should_retry logic ----

class TestShouldRetry:
    def test_transient_policy_retries_within_limit(self, classifier):
        assert classifier.should_retry(FailurePolicy.TRANSIENT, 0, max_retries=3) is True
        assert classifier.should_retry(FailurePolicy.TRANSIENT, 1, max_retries=3) is True
        assert classifier.should_retry(FailurePolicy.TRANSIENT, 2, max_retries=3) is True

    def test_transient_policy_stops_at_max(self, classifier):
        assert classifier.should_retry(FailurePolicy.TRANSIENT, 3, max_retries=3) is False
        assert classifier.should_retry(FailurePolicy.TRANSIENT, 4, max_retries=3) is False

    def test_permanent_policy_never_retries(self, classifier):
        assert classifier.should_retry(FailurePolicy.PERMANENT, 0) is False

    def test_degraded_policy_never_retries(self, classifier):
        assert classifier.should_retry(FailurePolicy.DEGRADED, 0) is False

    def test_skip_policy_never_retries(self, classifier):
        assert classifier.should_retry(FailurePolicy.SKIP, 0) is False

    def test_custom_max_retries(self, classifier):
        assert classifier.should_retry(FailurePolicy.TRANSIENT, 4, max_retries=5) is True
        assert classifier.should_retry(FailurePolicy.TRANSIENT, 5, max_retries=5) is False


# ---- 11. get_action returns meaningful strings ----

class TestGetAction:
    def test_transient_action(self, classifier):
        action = classifier.get_action(FailurePolicy.TRANSIENT)
        assert isinstance(action, str)
        assert "backoff" in action.lower() or "retry" in action.lower()

    def test_permanent_action(self, classifier):
        action = classifier.get_action(FailurePolicy.PERMANENT)
        assert isinstance(action, str)
        assert "stop" in action.lower()

    def test_degraded_action(self, classifier):
        action = classifier.get_action(FailurePolicy.DEGRADED)
        assert isinstance(action, str)
        assert "annotation" in action.lower() or "continue" in action.lower()

    def test_skip_action(self, classifier):
        action = classifier.get_action(FailurePolicy.SKIP)
        assert isinstance(action, str)
        assert "skip" in action.lower()

    def test_all_policies_have_actions(self, classifier):
        """Every FailurePolicy enum member must have an action string."""
        for policy in FailurePolicy:
            action = classifier.get_action(policy)
            assert isinstance(action, str)
            assert len(action) > 0


# ---- 12. Context edge cases ----

class TestContextEdgeCases:
    def test_empty_context_for_fnf_is_permanent(self, classifier):
        exc = FileNotFoundError("not found")
        assert classifier.classify(exc, {}) == FailurePolicy.PERMANENT

    def test_none_context_for_fnf_is_permanent(self, classifier):
        exc = FileNotFoundError("not found")
        assert classifier.classify(exc, None) == FailurePolicy.PERMANENT

    def test_unrelated_operation_for_fnf_is_permanent(self, classifier):
        exc = FileNotFoundError("not found")
        assert classifier.classify(exc, {"operation": "download"}) == FailurePolicy.PERMANENT

    def test_case_insensitive_operation(self, classifier):
        exc = FileNotFoundError("not found")
        assert classifier.classify(exc, {"operation": "Cleanup"}) == FailurePolicy.SKIP
        assert classifier.classify(exc, {"operation": "CLEANUP"}) == FailurePolicy.SKIP

    def test_windows_path_with_temp(self, classifier):
        exc = FileNotFoundError("not found")
        assert classifier.classify(exc, {"path": "C:\\Users\\temp\\audio.wav"}) == FailurePolicy.SKIP

    def test_path_without_temp_is_permanent(self, classifier):
        exc = FileNotFoundError("not found")
        assert classifier.classify(exc, {"path": "output/notes/ep01.md"}) == FailurePolicy.PERMANENT
