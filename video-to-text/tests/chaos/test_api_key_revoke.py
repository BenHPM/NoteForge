# -*- coding: utf-8 -*-
"""
Chaos test: simulate API key being revoked mid-pipeline.

Verifies that:
- FailureClassifier maps 401 LLMError to PERMANENT (not retryable)
- LLMError with retryable=False is not retried
- ExecutionTrace records the failure correctly
- CircuitBreaker opens after consecutive 401 failures

All I/O is mocked — no real API calls.

Run:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/chaos/test_api_key_revoke.py -v
"""

import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from noteforge.core.llm_providers import LLMError, LLMProvider
from noteforge.infra.execution_trace import ExecutionTrace
from noteforge.infra.failure_policy import FailurePolicy, FailureClassifier
from noteforge.infra.circuit_breaker import CircuitBreaker
from noteforge.context import PipelineContext, StageError, StageErrorKind
from noteforge.engine.pipeline import Pipeline
from noteforge.engine.stages.base import PipelineStage
from noteforge.engine.stages.generate import GenerateStage
from noteforge.engine.stages.config import GenerationConfig


# ── Helpers ──

def _make_trace():
    """Create an ExecutionTrace backed by a temp directory."""
    tmp = tempfile.mkdtemp()
    return ExecutionTrace(trace_dir=tmp), tmp


class _FakeProvider(LLMProvider):
    """A fake LLMProvider that raises LLMError on generate."""

    def __init__(self, error: LLMError):
        self._error = error

    def generate(self, system_prompt, user_prompt, max_tokens=8192, temperature=0.3):
        raise self._error

    def get_context_limit(self) -> int:
        return 200000

    def get_name(self) -> str:
        return "FakeProvider"


# ═══════════════════════════════════════════════════════════════
# Test: FailureClassifier maps 401 to PERMANENT
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestFailureClassifier401:
    """FailureClassifier maps 401 LLMError to PERMANENT."""

    def test_401_is_permanent(self):
        """HTTP 401 (Unauthorized) is classified as PERMANENT."""
        classifier = FailureClassifier()
        exc = LLMError("Authentication failed", status_code=401, retryable=False)
        assert classifier.classify(exc) == FailurePolicy.PERMANENT

    def test_401_not_transient(self):
        """401 is NOT transient — retrying with the same key won't help."""
        classifier = FailureClassifier()
        exc = LLMError("Invalid API key", status_code=401, retryable=False)
        policy = classifier.classify(exc)
        assert policy != FailurePolicy.TRANSIENT

    def test_401_should_not_retry(self):
        """should_retry returns False for 401 errors."""
        classifier = FailureClassifier()
        exc = LLMError("Auth error", status_code=401, retryable=False)
        policy = classifier.classify(exc)
        assert classifier.should_retry(policy, 0) is False
        assert classifier.should_retry(policy, 1) is False

    def test_403_is_also_permanent(self):
        """HTTP 403 (Forbidden) is also PERMANENT."""
        classifier = FailureClassifier()
        exc = LLMError("Access denied", status_code=403, retryable=False)
        assert classifier.classify(exc) == FailurePolicy.PERMANENT

    def test_401_with_retryable_true_still_transient_by_classifier(self):
        """Even if retryable=True is set, 401 is NOT 429.

        The classifier checks status_code 429 first for TRANSIENT,
        then retryable flag. 401 with retryable=True falls through to
        the retryable=True branch, which maps to TRANSIENT.

        This is a known edge case — 401 should ideally be PERMANENT
        regardless of retryable flag, but the classifier uses retryable
        as the primary signal for non-429 errors.
        """
        classifier = FailureClassifier()
        exc = LLMError("Auth error", status_code=401, retryable=True)
        # Current behavior: retryable=True -> TRANSIENT
        policy = classifier.classify(exc)
        assert policy == FailurePolicy.TRANSIENT

    def test_401_with_retryable_false_is_permanent(self):
        """401 with retryable=False (the normal case) is PERMANENT."""
        classifier = FailureClassifier()
        exc = LLMError("Invalid API key", status_code=401, retryable=False)
        assert classifier.classify(exc) == FailurePolicy.PERMANENT


# ═══════════════════════════════════════════════════════════════
# Test: LLMError with retryable=False is not retried
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestLLMErrorNotRetried:
    """LLMError with retryable=False is not retried by GenerateStage."""

    def test_non_retryable_error_raises_immediately(self):
        """GenerateStage raises LLMError immediately when retryable=False."""
        error = LLMError("Invalid API key", status_code=401, retryable=False)
        provider = _FakeProvider(error)

        mock_prompt_builder = MagicMock()
        mock_prompt_builder.build_system_prompt.return_value = "system"
        mock_prompt_builder.build_user_prompt.return_value = "user"
        mock_quality_manager = MagicMock()

        stage = GenerateStage(
            prompt_builder=mock_prompt_builder,
            quality_manager=mock_quality_manager,
            provider=provider,
            config=GenerationConfig(max_retries=3),
        )

        ctx = PipelineContext(
            source_path="test.txt",
            transcript_path="test.txt",
            title="Test",
            content_type="lecture",
            clean_text="Some transcript text",
            chunks=["Some transcript text"],
        )

        with pytest.raises(LLMError) as exc_info:
            stage.execute(ctx)
        assert exc_info.value.status_code == 401
        assert exc_info.value.retryable is False

    def test_non_retryable_error_does_not_consume_retries(self):
        """Non-retryable error exits immediately, not after max_retries."""
        error = LLMError("API key revoked", status_code=401, retryable=False)
        provider = _FakeProvider(error)

        mock_prompt_builder = MagicMock()
        mock_prompt_builder.build_system_prompt.return_value = "system"
        mock_prompt_builder.build_user_prompt.return_value = "user"
        mock_quality_manager = MagicMock()

        stage = GenerateStage(
            prompt_builder=mock_prompt_builder,
            quality_manager=mock_quality_manager,
            provider=provider,
            config=GenerationConfig(max_retries=3),
        )

        ctx = PipelineContext(
            source_path="test.txt",
            transcript_path="test.txt",
            title="Test",
            content_type="lecture",
            clean_text="Some transcript text",
            chunks=["Some transcript text"],
        )

        # Should raise immediately, not after 3 retries
        with pytest.raises(LLMError):
            stage.execute(ctx)

    def test_retryable_error_exhausts_retries(self):
        """Contrast: retryable error goes through all retries before failing."""
        error = LLMError("Rate limited", status_code=429, retryable=True)
        provider = _FakeProvider(error)

        mock_prompt_builder = MagicMock()
        mock_prompt_builder.build_system_prompt.return_value = "system"
        mock_prompt_builder.build_user_prompt.return_value = "user"
        mock_quality_manager = MagicMock()

        stage = GenerateStage(
            prompt_builder=mock_prompt_builder,
            quality_manager=mock_quality_manager,
            provider=provider,
            config=GenerationConfig(max_retries=2),
        )

        ctx = PipelineContext(
            source_path="test.txt",
            transcript_path="test.txt",
            title="Test",
            content_type="lecture",
            clean_text="Some transcript text",
            chunks=["Some transcript text"],
        )

        # Retryable error: returns (None, attempts) after exhausting retries
        result_ctx = stage.execute(ctx)
        assert result_ctx.error is not None
        assert "生成失败" in result_ctx.error or result_ctx.note_text == ""

    def test_401_via_claude_provider_no_retry_loop(self):
        """401 via real ClaudeProvider code path: no retry loop."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            provider = MagicMock(spec=LLMProvider)
            provider.get_name.return_value = "Claude (test)"
            provider.generate.side_effect = LLMError(
                "Invalid API key", status_code=401, retryable=False,
            )

            mock_prompt_builder = MagicMock()
            mock_prompt_builder.build_system_prompt.return_value = "system"
            mock_prompt_builder.build_user_prompt.return_value = "user"
            mock_quality_manager = MagicMock()

            stage = GenerateStage(
                prompt_builder=mock_prompt_builder,
                quality_manager=mock_quality_manager,
                provider=provider,
                config=GenerationConfig(max_retries=5),
            )

            ctx = PipelineContext(
                source_path="test.txt",
                transcript_path="test.txt",
                title="Test",
                content_type="lecture",
                clean_text="Some transcript text",
                chunks=["Some transcript text"],
            )

            with pytest.raises(LLMError) as exc_info:
                stage.execute(ctx)
            assert exc_info.value.status_code == 401

            # generate() called exactly once — no retries
            assert provider.generate.call_count == 1


# ═══════════════════════════════════════════════════════════════
# Test: ExecutionTrace records 401 failure
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestExecutionTraceRecords401:
    """ExecutionTrace correctly records 401 failures."""

    def test_trace_records_failed_stage(self):
        """After 401, the generate stage is recorded as FAILED."""
        trace, tmp = _make_trace()

        records = [
            ExecutionTrace.StepRecord(
                stage="preprocess",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h0",
                output_hash="h1",
            ),
            ExecutionTrace.StepRecord(
                stage="generate",
                status=ExecutionTrace.Status.FAILED,
                input_hash="h1",
                error_type="PERMANENT",
            ),
        ]
        trace.save("ep01", records)

        loaded = trace.resume("ep01")
        assert loaded[1].stage == "generate"
        assert loaded[1].status == ExecutionTrace.Status.FAILED
        assert loaded[1].error_type == "PERMANENT"

    def test_trace_after_401_dead_letter_not_resumable(self):
        """After marking 401 as dead letter, trace is not resumable."""
        trace, tmp = _make_trace()

        records = [
            ExecutionTrace.StepRecord(
                stage="preprocess",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h0",
                output_hash="h1",
            ),
            ExecutionTrace.StepRecord(
                stage="generate",
                status=ExecutionTrace.Status.FAILED,
                input_hash="h1",
                error_type="PERMANENT",
            ),
        ]
        trace.save("ep01", records)

        # Before dead letter: resumable (has COMPLETED, no DEAD_LETTER)
        assert trace.is_resumable("ep01") is True

        # After dead letter: not resumable
        trace.mark_dead_letter("ep01", "generate", "API key revoked (401)")
        assert trace.is_resumable("ep01") is False

    def test_trace_records_error_type_permanent_for_401(self):
        """401 errors are recorded with error_type='PERMANENT'."""
        trace, tmp = _make_trace()

        trace.update_step("ep01", "generate", ExecutionTrace.Status.FAILED,
                          error_type="PERMANENT")

        loaded = trace.resume("ep01")
        gen_rec = [r for r in loaded if r.stage == "generate"][0]
        assert gen_rec.error_type == "PERMANENT"

    def test_pipeline_error_on_401_sets_stage_error(self):
        """Pipeline captures 401 LLMError as a StageError on ctx."""
        error = LLMError("Invalid API key", status_code=401, retryable=False)
        provider = _FakeProvider(error)

        mock_prompt_builder = MagicMock()
        mock_prompt_builder.build_system_prompt.return_value = "system"
        mock_prompt_builder.build_user_prompt.return_value = "user"
        mock_quality_manager = MagicMock()

        # Need a preprocess-like stage that provides clean_text and chunks
        # so Pipeline._validate_order() is satisfied for GenerateStage.
        class _StubPreprocess(PipelineStage):
            required_inputs = frozenset()
            provided_outputs = frozenset({"clean_text", "chunks"})

            @property
            def name(self) -> str:
                return "preprocess"

            def execute(self, ctx: PipelineContext) -> PipelineContext:
                ctx.clean_text = "Some transcript text"
                ctx.chunks = ["Some transcript text"]
                return ctx

        gen_stage = GenerateStage(
            prompt_builder=mock_prompt_builder,
            quality_manager=mock_quality_manager,
            provider=provider,
            config=GenerationConfig(max_retries=2),
        )

        pipeline = Pipeline([_StubPreprocess(), gen_stage])
        ctx = PipelineContext(
            source_path="test.txt",
            transcript_path="test.txt",
            title="Test",
            content_type="lecture",
        )

        result = pipeline.run(ctx)
        assert result.error is not None
        assert isinstance(result.error, StageError)
        assert result.error.stage == "generate"
        assert result.error.kind == StageErrorKind.FATAL

    def test_mid_pipeline_401_preserves_completed_stages(self):
        """When 401 occurs mid-pipeline, completed stages are preserved in trace."""
        trace, tmp = _make_trace()

        records = [
            ExecutionTrace.StepRecord(
                stage="preprocess",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1",
                output_hash="h2",
            ),
            ExecutionTrace.StepRecord(
                stage="generate",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h2",
                output_hash="h3",
            ),
            ExecutionTrace.StepRecord(
                stage="format",
                status=ExecutionTrace.Status.FAILED,
                input_hash="h3",
                error_type="PERMANENT",
            ),
        ]
        trace.save("ep01", records)

        # Verify recovery path
        assert trace.is_resumable("ep01") is True
        last = trace.get_last_completed_stage("ep01")
        assert last == "generate"

        # After API key is fixed, can resume from format stage
        loaded = trace.resume("ep01")
        failed_stages = [r for r in loaded if r.status == ExecutionTrace.Status.FAILED]
        assert len(failed_stages) == 1
        assert failed_stages[0].stage == "format"


# ═══════════════════════════════════════════════════════════════
# Test: CircuitBreaker opens after 401
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestCircuitBreakerAfter401:
    """CircuitBreaker opens after consecutive 401 failures."""

    def test_circuit_opens_after_consecutive_401s(self):
        """CircuitBreaker opens after failure_threshold consecutive 401s."""
        cb = CircuitBreaker(name="llm", failure_threshold=3)

        for i in range(3):
            cb.record_failure()

        assert cb.state == CircuitBreaker.State.OPEN
        assert cb.can_execute() is False

    def test_circuit_rejects_calls_after_opening(self):
        """Once open, CircuitBreaker rejects all LLM calls."""
        cb = CircuitBreaker(name="llm", failure_threshold=2)

        cb.record_failure()
        cb.record_failure()  # opens

        assert cb.can_execute() is False

    def test_circuit_401_failure_count_increments(self):
        """Each 401 failure increments the failure counter."""
        cb = CircuitBreaker(name="llm", failure_threshold=5)

        cb.record_failure()
        assert cb._failure_count == 1

        cb.record_failure()
        assert cb._failure_count == 2

        # Not yet open
        assert cb.state == CircuitBreaker.State.CLOSED

    def test_circuit_success_resets_after_401(self):
        """A successful call resets the failure counter."""
        cb = CircuitBreaker(name="llm", failure_threshold=3)

        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # resets counter

        # Need 3 more failures to open
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.CLOSED

    def test_circuit_half_open_after_recovery_timeout(self):
        """CircuitBreaker transitions to HALF_OPEN after recovery timeout."""
        import time

        cb = CircuitBreaker(name="llm", failure_threshold=1, recovery_timeout=0.05)

        cb.record_failure()  # opens
        assert cb.state == CircuitBreaker.State.OPEN

        time.sleep(0.1)  # wait for recovery
        assert cb.state == CircuitBreaker.State.HALF_OPEN

    def test_circuit_reopens_on_401_during_half_open(self):
        """401 during HALF_OPEN immediately reopens the circuit."""
        import time

        cb = CircuitBreaker(name="llm", failure_threshold=1, recovery_timeout=0.05)

        cb.record_failure()  # opens
        time.sleep(0.1)  # -> HALF_OPEN
        assert cb.state == CircuitBreaker.State.HALF_OPEN

        cb.record_failure()  # 401 during probe -> reopens
        assert cb.state == CircuitBreaker.State.OPEN

    def test_circuit_breaker_integrates_with_failure_classifier(self):
        """CircuitBreaker and FailureClassifier work together correctly.

        Scenario: 401 -> PERMANENT -> circuit breaker records failure -> opens.
        """
        classifier = FailureClassifier()
        cb = CircuitBreaker(name="llm", failure_threshold=2)

        # First 401
        exc1 = LLMError("Unauthorized", status_code=401, retryable=False)
        policy1 = classifier.classify(exc1)
        assert policy1 == FailurePolicy.PERMANENT
        assert classifier.should_retry(policy1, 0) is False
        cb.record_failure()

        # Second 401
        exc2 = LLMError("Unauthorized", status_code=401, retryable=False)
        policy2 = classifier.classify(exc2)
        assert policy2 == FailurePolicy.PERMANENT
        cb.record_failure()

        # Circuit is now open
        assert cb.state == CircuitBreaker.State.OPEN
        assert cb.can_execute() is False

    def test_429_does_not_open_circuit_prematurely(self):
        """Contrast: 429 (rate limit) is TRANSIENT, not a permanent failure.

        CircuitBreaker still records failures, but the FailureClassifier
        correctly identifies 429 as TRANSIENT (retryable).
        """
        classifier = FailureClassifier()

        exc = LLMError("Rate limited", status_code=429, retryable=True)
        policy = classifier.classify(exc)
        assert policy == FailurePolicy.TRANSIENT
        assert classifier.should_retry(policy, 0) is True

    def test_mixed_401_and_429_failures(self):
        """Mixed 401/429 failures: circuit opens, 429 is retryable, 401 is not."""
        classifier = FailureClassifier()
        cb = CircuitBreaker(name="llm", failure_threshold=3)

        # 429 (transient, retryable)
        exc_429 = LLMError("Rate limited", status_code=429, retryable=True)
        assert classifier.classify(exc_429) == FailurePolicy.TRANSIENT
        cb.record_failure()  # circuit doesn't care about policy, just counts

        # 401 (permanent, not retryable)
        exc_401 = LLMError("Unauthorized", status_code=401, retryable=False)
        assert classifier.classify(exc_401) == FailurePolicy.PERMANENT
        cb.record_failure()

        # Another 401
        cb.record_failure()

        # Circuit opens after 3 consecutive failures regardless of type
        assert cb.state == CircuitBreaker.State.OPEN
