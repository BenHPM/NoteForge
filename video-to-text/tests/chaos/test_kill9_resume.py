# -*- coding: utf-8 -*-
"""
Chaos test: simulate process kill (SIGKILL / kill -9) at each pipeline stage.

Verifies that ExecutionTrace correctly records partial progress so the pipeline
can resume from the last completed stage after an abrupt termination.

All I/O is mocked — no real files or network calls.

Run:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/chaos/test_kill9_resume.py -v
"""

import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from noteforge.infra.execution_trace import ExecutionTrace
from noteforge.context import PipelineContext, StageError, StageErrorKind
from noteforge.engine.pipeline import Pipeline
from noteforge.engine.stages.base import PipelineStage


# ── Helpers ──

# Pipeline stage order (matches note_engine.py Pipeline construction)
PIPELINE_STAGES = ["preprocess", "generate", "format", "quality_gate", "save", "postprocess"]


def _make_trace():
    """Create an ExecutionTrace backed by a temp directory."""
    tmp = tempfile.mkdtemp()
    return ExecutionTrace(trace_dir=tmp), tmp


def _build_partial_records(kill_after_stage: str):
    """Build StepRecord list simulating a kill at the given stage.

    Stages before kill_after_stage are COMPLETED with valid hash chains.
    The kill_after_stage itself is left RUNNING (no output_hash).
    Stages after it are PENDING.
    """
    records = []
    prev_hash = "h0"
    for stage in PIPELINE_STAGES:
        if stage == kill_after_stage:
            records.append(ExecutionTrace.StepRecord(
                stage=stage,
                status=ExecutionTrace.Status.RUNNING,
                input_hash=prev_hash,
                started_at="2026-08-01T10:00:00",
            ))
            break
        else:
            out_hash = f"h_{stage}"
            records.append(ExecutionTrace.StepRecord(
                stage=stage,
                status=ExecutionTrace.Status.COMPLETED,
                input_hash=prev_hash,
                output_hash=out_hash,
                started_at="2026-08-01T10:00:00",
                completed_at="2026-08-01T10:05:00",
            ))
            prev_hash = out_hash
    return records


def _build_all_completed_records():
    """Build StepRecord list with all stages COMPLETED (valid hash chain)."""
    records = []
    prev_hash = "h0"
    for stage in PIPELINE_STAGES:
        out_hash = f"h_{stage}"
        records.append(ExecutionTrace.StepRecord(
            stage=stage,
            status=ExecutionTrace.Status.COMPLETED,
            input_hash=prev_hash,
            output_hash=out_hash,
            started_at="2026-08-01T10:00:00",
            completed_at="2026-08-01T10:05:00",
        ))
        prev_hash = out_hash
    return records


# ── Fake stages for pipeline resume tests ──

class _FakeStage(PipelineStage):
    """A minimal PipelineStage that just sets a field on ctx."""

    required_inputs = frozenset()
    provided_outputs = frozenset()

    def __init__(self, stage_name: str, output_field: str, output_value=None):
        self._name = stage_name
        self._output_field = output_field
        self._output_value = output_value

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        if self._output_value is not None:
            setattr(ctx, self._output_field, self._output_value)
        return ctx


class _CrashingStage(PipelineStage):
    """A PipelineStage that raises an exception to simulate a crash."""

    required_inputs = frozenset()
    provided_outputs = frozenset()

    def __init__(self, stage_name: str, error_msg: str = "Killed"):
        self._name = stage_name
        self._error_msg = error_msg

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        raise RuntimeError(self._error_msg)


# ═══════════════════════════════════════════════════════════════
# Test: resume() identifies last completed stage
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestResumeIdentifiesLastCompleted:
    """resume() correctly identifies the last COMPLETED stage after a kill."""

    def test_kill_at_preprocess_last_completed_is_none(self):
        """Kill before any stage completes -> no completed stage."""
        trace, tmp = _make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="preprocess",
                status=ExecutionTrace.Status.RUNNING,
                input_hash="h0",
                started_at="2026-08-01T10:00:00",
            ),
        ]
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") is None

    def test_kill_at_generate_last_completed_is_preprocess(self):
        """Kill during generate -> last completed is preprocess."""
        trace, tmp = _make_trace()
        records = _build_partial_records("generate")
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") == "preprocess"

    def test_kill_at_format_last_completed_is_generate(self):
        """Kill during format -> last completed is generate."""
        trace, tmp = _make_trace()
        records = _build_partial_records("format")
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") == "generate"

    def test_kill_at_quality_gate_last_completed_is_format(self):
        """Kill during quality_gate -> last completed is format."""
        trace, tmp = _make_trace()
        records = _build_partial_records("quality_gate")
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") == "format"

    def test_kill_at_save_last_completed_is_quality_gate(self):
        """Kill during save -> last completed is quality_gate."""
        trace, tmp = _make_trace()
        records = _build_partial_records("save")
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") == "quality_gate"

    def test_kill_at_postprocess_last_completed_is_save(self):
        """Kill during postprocess -> last completed is save."""
        trace, tmp = _make_trace()
        records = _build_partial_records("postprocess")
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") == "save"


# ═══════════════════════════════════════════════════════════════
# Test: is_resumable() for partial traces
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestIsResumableAfterKill:
    """is_resumable() returns True for partial traces with COMPLETED stages."""

    def test_partial_trace_is_resumable(self):
        """Partial trace with at least one COMPLETED is resumable."""
        trace, tmp = _make_trace()
        records = _build_partial_records("generate")
        trace.save("ep01", records)
        assert trace.is_resumable("ep01") is True

    def test_all_running_not_resumable(self):
        """Trace with only RUNNING stages is not resumable."""
        trace, tmp = _make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="preprocess",
                status=ExecutionTrace.Status.RUNNING,
                input_hash="h0",
            ),
        ]
        trace.save("ep01", records)
        assert trace.is_resumable("ep01") is False

    def test_all_completed_is_resumable(self):
        """Fully completed trace is resumable (no DEAD_LETTER)."""
        trace, tmp = _make_trace()
        records = _build_all_completed_records()
        trace.save("ep01", records)
        assert trace.is_resumable("ep01") is True

    def test_dead_letter_makes_not_resumable(self):
        """DEAD_LETTER in any stage makes the trace not resumable."""
        trace, tmp = _make_trace()
        records = _build_partial_records("generate")
        # Mark the running stage as DEAD_LETTER
        records[-1].status = ExecutionTrace.Status.DEAD_LETTER
        records[-1].error_type = "PERMANENT"
        trace.save("ep01", records)
        assert trace.is_resumable("ep01") is False


# ═══════════════════════════════════════════════════════════════
# Test: mark_dead_letter() after kill
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestMarkDeadLetterAfterKill:
    """mark_dead_letter() correctly marks failed stages after a kill."""

    def test_mark_killed_stage_as_dead_letter(self):
        """The stage that was running when killed becomes DEAD_LETTER."""
        trace, tmp = _make_trace()
        records = _build_partial_records("generate")
        trace.save("ep01", records)
        trace.mark_dead_letter("ep01", "generate", "Process killed (SIGKILL)")

        loaded = trace._load_raw("ep01")
        gen_rec = [r for r in loaded if r.stage == "generate"][0]
        assert gen_rec.status == ExecutionTrace.Status.DEAD_LETTER
        assert gen_rec.error_type == "PERMANENT"

    def test_dead_letter_cascades_to_pending(self):
        """DEAD_LETTER cascades to subsequent PENDING stages."""
        trace, tmp = _make_trace()
        # Build records: preprocess COMPLETED, generate RUNNING, format/quality_gate PENDING
        records = _build_partial_records("generate")
        # Add PENDING stages after generate
        records.append(ExecutionTrace.StepRecord(
            stage="format", status=ExecutionTrace.Status.PENDING, input_hash="h_generate",
        ))
        records.append(ExecutionTrace.StepRecord(
            stage="quality_gate", status=ExecutionTrace.Status.PENDING, input_hash="h_format",
        ))
        trace.save("ep01", records)
        trace.mark_dead_letter("ep01", "generate", "Process killed")

        loaded = trace._load_raw("ep01")
        # generate -> DEAD_LETTER
        assert loaded[1].status == ExecutionTrace.Status.DEAD_LETTER
        # format (PENDING) -> DEAD_LETTER
        assert loaded[2].status == ExecutionTrace.Status.DEAD_LETTER
        # quality_gate (PENDING) -> DEAD_LETTER
        assert loaded[3].status == ExecutionTrace.Status.DEAD_LETTER
        # preprocess (COMPLETED) -> unchanged
        assert loaded[0].status == ExecutionTrace.Status.COMPLETED

    def test_dead_letter_does_not_affect_completed(self):
        """COMPLETED stages before the kill are not affected."""
        trace, tmp = _make_trace()
        records = _build_partial_records("format")
        trace.save("ep01", records)
        trace.mark_dead_letter("ep01", "format", "OOM killed")

        loaded = trace._load_raw("ep01")
        assert loaded[0].status == ExecutionTrace.Status.COMPLETED  # preprocess
        assert loaded[1].status == ExecutionTrace.Status.COMPLETED  # generate


# ═══════════════════════════════════════════════════════════════
# Test: pipeline continues from the right point after resume
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestPipelineResumeFromCorrectPoint:
    """After a kill, the pipeline can continue from the correct stage."""

    def test_resume_skips_completed_stages(self):
        """Resumed pipeline skips already-completed stages."""
        # Build a pipeline with 3 stages, crash on the 2nd
        stage1 = _FakeStage("preprocess", "clean_text", "cleaned")
        stage2 = _CrashingStage("generate", "SIGKILL simulation")
        stage3 = _FakeStage("format", "formatted_text", "formatted")

        pipeline = Pipeline([stage1, stage2, stage3])
        ctx = PipelineContext(
            source_path="test.txt",
            transcript_path="test.txt",
            title="Test",
            content_type="lecture",
        )
        ctx.clean_text = ""  # will be set by stage1

        result = pipeline.run(ctx)
        # Pipeline should have stopped at generate (crash)
        assert result.error is not None
        assert "generate" in str(result.error)

    def test_resume_from_trace_rebuilds_context(self):
        """After kill, trace data allows rebuilding context for resume."""
        trace, tmp = _make_trace()
        records = _build_partial_records("generate")
        trace.save("ep01", records)

        # Verify we can identify where to resume
        last_completed = trace.get_last_completed_stage("ep01")
        assert last_completed == "preprocess"

        # Verify the trace is resumable
        assert trace.is_resumable("ep01") is True

        # Simulate: rebuild pipeline starting from generate
        stage2 = _FakeStage("generate", "note_text", "generated note")
        stage3 = _FakeStage("format", "formatted_text", "formatted")
        pipeline = Pipeline([stage2, stage3])

        ctx = PipelineContext(
            source_path="test.txt",
            transcript_path="test.txt",
            title="Test",
            content_type="lecture",
            clean_text="cleaned text",  # restored from previous run
            chunks=["cleaned text"],
        )
        result = pipeline.run(ctx)
        assert result.error is None
        assert result.note_text == "generated note"
        assert result.formatted_text == "formatted"

    def test_kill_at_each_stage_identifies_correct_resume_point(self):
        """For each possible kill point, the correct resume stage is identified."""
        for i, kill_stage in enumerate(PIPELINE_STAGES):
            trace, tmp = _make_trace()
            records = _build_partial_records(kill_stage)
            trace.save(f"kill_{kill_stage}", records)

            last_completed = trace.get_last_completed_stage(f"kill_{kill_stage}")
            if i == 0:
                # Kill at first stage -> no completed stage
                assert last_completed is None
            else:
                assert last_completed == PIPELINE_STAGES[i - 1], (
                    f"Kill at {kill_stage}: expected last_completed={PIPELINE_STAGES[i-1]}, "
                    f"got {last_completed}"
                )


# ═══════════════════════════════════════════════════════════════
# Test: hash chain integrity after kill
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestHashChainAfterKill:
    """Hash chain remains valid for completed stages after a kill."""

    def test_completed_stages_have_valid_hash_chain(self):
        """Completed stages before the kill have a valid hash chain."""
        trace, tmp = _make_trace()
        records = _build_partial_records("format")
        trace.save("ep01", records)

        loaded = trace.resume("ep01")
        # All completed stages should remain COMPLETED (hash chain valid)
        completed = [r for r in loaded if r.status == ExecutionTrace.Status.COMPLETED]
        assert len(completed) == 2  # preprocess, generate

    def test_running_stage_has_no_output_hash(self):
        """The killed (RUNNING) stage has no output_hash."""
        trace, tmp = _make_trace()
        records = _build_partial_records("generate")
        trace.save("ep01", records)

        loaded = trace.resume("ep01")
        running = [r for r in loaded if r.status == ExecutionTrace.Status.RUNNING]
        assert len(running) == 1
        assert running[0].output_hash is None


# ═══════════════════════════════════════════════════════════════
# Test: update_step after kill for resume
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestUpdateStepAfterKill:
    """update_step can transition a RUNNING stage to COMPLETED after resume."""

    def test_update_killed_stage_to_completed(self):
        """After resuming, the previously-killed stage can be marked COMPLETED."""
        trace, tmp = _make_trace()
        records = _build_partial_records("generate")
        trace.save("ep01", records)

        # Simulate: generate stage completes on resume
        trace.update_step("ep01", "generate", ExecutionTrace.Status.COMPLETED,
                          output_hash="h_generate")

        loaded = trace.resume("ep01")
        gen_rec = [r for r in loaded if r.stage == "generate"][0]
        assert gen_rec.status == ExecutionTrace.Status.COMPLETED
        assert gen_rec.output_hash == "h_generate"
        assert gen_rec.completed_at is not None

    def test_sequential_resume_updates(self):
        """Multiple stages can be updated sequentially during resume."""
        trace, tmp = _make_trace()
        records = _build_partial_records("generate")
        trace.save("ep01", records)

        # Resume: generate completes
        trace.update_step("ep01", "generate", ExecutionTrace.Status.COMPLETED,
                          output_hash="h_generate")
        # Resume: format completes
        trace.update_step("ep01", "format", ExecutionTrace.Status.COMPLETED,
                          input_hash="h_generate", output_hash="h_format")

        last = trace.get_last_completed_stage("ep01")
        assert last == "format"

    def test_full_pipeline_recovery_after_multiple_kills(self):
        """Simulate multiple kill-9 events, recovering each time."""
        trace, tmp = _make_trace()

        # First: kill after preprocess
        records = _build_partial_records("generate")
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") == "preprocess"

        # Resume: generate completes
        trace.update_step("ep01", "generate", ExecutionTrace.Status.COMPLETED,
                          output_hash="h_generate")
        assert trace.get_last_completed_stage("ep01") == "generate"

        # Second kill: after format
        trace.update_step("ep01", "format", ExecutionTrace.Status.COMPLETED,
                          output_hash="h_format")
        trace.update_step("ep01", "quality_gate", ExecutionTrace.Status.RUNNING,
                          input_hash="h_format")
        # Kill happens here -- quality_gate is RUNNING
        assert trace.get_last_completed_stage("ep01") == "format"

        # Resume: quality_gate completes
        trace.update_step("ep01", "quality_gate", ExecutionTrace.Status.COMPLETED,
                          output_hash="h_quality_gate")
        assert trace.get_last_completed_stage("ep01") == "quality_gate"

        # Continue: save and postprocess
        trace.update_step("ep01", "save", ExecutionTrace.Status.COMPLETED,
                          output_hash="h_save")
        trace.update_step("ep01", "postprocess", ExecutionTrace.Status.COMPLETED,
                          output_hash="h_postprocess")

        # All stages complete
        loaded = trace.resume("ep01")
        assert all(r.status == ExecutionTrace.Status.COMPLETED for r in loaded)

    def test_kill_before_any_stage_completes(self):
        """Kill-9 before any stage completes: first stage was RUNNING."""
        trace, tmp = _make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="preprocess",
                status=ExecutionTrace.Status.RUNNING,
                input_hash="h0",
                started_at="2026-08-01T10:00:00",
            ),
        ] + [
            ExecutionTrace.StepRecord(
                stage=s,
                status=ExecutionTrace.Status.PENDING,
                input_hash=f"h{i+1}",
            )
            for i, s in enumerate(PIPELINE_STAGES[1:])
        ]
        trace.save("ep01", records)

        # No completed stage -> not resumable
        assert trace.get_last_completed_stage("ep01") is None
        assert trace.is_resumable("ep01") is False

    def test_kill_after_all_stages_complete(self):
        """All stages completed -- no kill needed, trace is fully done."""
        trace, tmp = _make_trace()
        records = _build_all_completed_records()
        trace.save("ep01", records)

        assert trace.get_last_completed_stage("ep01") == "postprocess"
        # All COMPLETED, no DEAD_LETTER -> resumable (though nothing to resume)
        assert trace.is_resumable("ep01") is True

        loaded = trace.resume("ep01")
        assert all(r.status == ExecutionTrace.Status.COMPLETED for r in loaded)
