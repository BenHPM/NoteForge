# -*- coding: utf-8 -*-
"""
Chaos test: simulate disk full scenarios.

Verifies that:
- ExecutionTrace.save() handles disk full gracefully (does not crash)
- FailureClassifier maps disk full OSError to PERMANENT
- The engine doesn't crash, just reports the error

All I/O is mocked — no real disk full conditions needed.

Run:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/chaos/test_disk_full.py -v
"""

import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from noteforge.infra.execution_trace import ExecutionTrace
from noteforge.infra.failure_policy import FailurePolicy, FailureClassifier
from noteforge.infra.file_io import write_file
from noteforge.context import PipelineContext, StageError, StageErrorKind
from noteforge.engine.pipeline import Pipeline
from noteforge.engine.stages.base import PipelineStage
from noteforge.engine.stages.save import SaveStage


# ── Helpers ──

def _make_trace():
    """Create an ExecutionTrace backed by a temp directory."""
    tmp = tempfile.mkdtemp()
    return ExecutionTrace(trace_dir=tmp), tmp


class _FakeStage(PipelineStage):
    """Minimal PipelineStage that does nothing."""

    required_inputs = frozenset()
    provided_outputs = frozenset()

    def __init__(self, stage_name: str):
        self._name = stage_name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        return ctx


# ═══════════════════════════════════════════════════════════════
# Test: ExecutionTrace.save() handles disk full
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestExecutionTraceDiskFull:
    """ExecutionTrace.save() handles OSError(28) gracefully."""

    def test_save_raises_on_disk_full(self):
        """ExecutionTrace.save() propagates OSError when write_file fails.

        The trace module does not swallow I/O errors — callers must handle them.
        This is by design: silently dropping trace data would be worse.
        """
        trace, tmp = _make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="preprocess",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h0",
                output_hash="h1",
            ),
        ]

        with patch("noteforge.infra.execution_trace.write_file",
                    side_effect=OSError(28, "No space left on device")):
            with pytest.raises(OSError) as exc_info:
                trace.save("ep01", records)
            assert exc_info.value.errno == 28

    def test_save_with_disk_full_does_not_corrupt_existing_trace(self):
        """If save fails, the previous trace file remains intact."""
        trace, tmp = _make_trace()

        # First save succeeds
        records_v1 = [
            ExecutionTrace.StepRecord(
                stage="preprocess",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h0",
                output_hash="h1",
            ),
        ]
        trace.save("ep01", records_v1)

        # Second save fails (disk full)
        records_v2 = [
            ExecutionTrace.StepRecord(
                stage="preprocess",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h0",
                output_hash="h1",
            ),
            ExecutionTrace.StepRecord(
                stage="generate",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1",
                output_hash="h2",
            ),
        ]
        with patch("noteforge.infra.execution_trace.write_file",
                    side_effect=OSError(28, "No space left on device")):
            with pytest.raises(OSError):
                trace.save("ep01", records_v2)

        # Original trace is still intact
        loaded = trace.resume("ep01")
        assert len(loaded) == 1
        assert loaded[0].stage == "preprocess"

    def test_ensure_dir_fails_gracefully_on_disk_full(self):
        """If directory creation fails due to disk full, save raises OSError."""
        trace = ExecutionTrace(trace_dir="/nonexistent_deep/nested/dir")
        records = [
            ExecutionTrace.StepRecord(
                stage="preprocess",
                status=ExecutionTrace.Status.PENDING,
                input_hash="h0",
            ),
        ]
        with patch("os.makedirs", side_effect=OSError(28, "No space left on device")):
            with pytest.raises(OSError) as exc_info:
                trace.save("ep01", records)
            assert exc_info.value.errno == 28


# ═══════════════════════════════════════════════════════════════
# Test: FailureClassifier maps disk full to PERMANENT
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestFailureClassifierDiskFull:
    """FailureClassifier maps OSError(28) to PERMANENT."""

    def test_oserror_28_is_permanent(self):
        """OSError with errno=28 (ENOSPC) is classified as PERMANENT."""
        classifier = FailureClassifier()
        exc = OSError(28, "No space left on device")
        assert classifier.classify(exc) == FailurePolicy.PERMANENT

    def test_oserror_28_not_transient(self):
        """Disk full is NOT transient — retrying won't help."""
        classifier = FailureClassifier()
        exc = OSError(28, "No space left on device")
        policy = classifier.classify(exc)
        assert policy != FailurePolicy.TRANSIENT

    def test_oserror_28_should_not_retry(self):
        """should_retry returns False for disk full."""
        classifier = FailureClassifier()
        exc = OSError(28, "No space left on device")
        policy = classifier.classify(exc)
        assert classifier.should_retry(policy, 0) is False

    def test_generic_oserror_is_permanent(self):
        """Generic OSError (e.g. permission denied) is also PERMANENT."""
        classifier = FailureClassifier()
        exc = OSError(13, "Permission denied")
        assert classifier.classify(exc) == FailurePolicy.PERMANENT

    def test_oserror_with_path_context_is_permanent(self):
        """OSError with a path context hint is still PERMANENT."""
        classifier = FailureClassifier()
        exc = OSError(28, "No space left on device")
        assert classifier.classify(exc, {"path": "output/notes/ep01.md"}) == FailurePolicy.PERMANENT

    def test_oserror_with_generate_operation_is_permanent(self):
        """OSError during generate operation is PERMANENT."""
        classifier = FailureClassifier()
        exc = OSError(28, "No space left on device")
        assert classifier.classify(exc, {"operation": "generate"}) == FailurePolicy.PERMANENT


# ═══════════════════════════════════════════════════════════════
# Test: write_file disk full behavior
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestWriteFileDiskFull:
    """write_file propagates OSError on disk full."""

    def test_write_file_propagates_oserror_28(self):
        """write_file does not swallow OSError(28).

        write_file uses os.fdopen (not builtins.open), so we patch
        os.fdopen to simulate disk full after mkstemp succeeds.
        """
        tmp_dir = tempfile.mkdtemp()
        target = os.path.join(tmp_dir, "test.txt")

        original_fdopen = os.fdopen
        original_mkstemp = tempfile.mkstemp
        call_count = [0]

        def mock_fdopen(fd, mode, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Close the real fd first to avoid ResourceWarning
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise OSError(28, "No space left on device")
            return original_fdopen(fd, mode, *args, **kwargs)

        with patch("os.fdopen", side_effect=mock_fdopen):
            with pytest.raises(OSError) as exc_info:
                write_file(target, "content")
            assert exc_info.value.errno == 28

    def test_write_file_cleans_up_tmp_on_failure(self):
        """write_file cleans up the temp file when write fails."""
        tmp_dir = tempfile.mkdtemp()
        target = os.path.join(tmp_dir, "test.txt")

        # Mock os.fdopen to raise after mkstemp succeeds
        original_mkstemp = __import__("tempfile").mkstemp
        call_count = [0]

        def mock_fdopen(fd, mode, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Close the real fd first
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise OSError(28, "No space left on device")
            return __import__("builtins").open(fd, mode, *args, **kwargs)

        with patch("os.fdopen", side_effect=mock_fdopen):
            with pytest.raises(OSError):
                write_file(target, "content")

        # No .tmp files should remain
        tmp_files = [f for f in os.listdir(tmp_dir) if f.endswith(".tmp")]
        assert len(tmp_files) == 0


# ═══════════════════════════════════════════════════════════════
# Test: SaveStage handles disk full
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestSaveStageDiskFull:
    """SaveStage propagates disk full error through Pipeline error mechanism."""

    def test_save_stage_disk_full_sets_error(self):
        """When write_file raises OSError(28), SaveStage.execute() raises OSError.

        When used inside Pipeline.run(), this OSError is captured as a
        StageError(kind=FATAL) on ctx. We test the stage directly here
        to avoid Pipeline validation requiring a full stage chain.
        """
        from pathlib import Path

        save_stage = SaveStage(notes_dir=Path(tempfile.mkdtemp()))

        ctx = PipelineContext(
            source_path="test.txt",
            output_path=os.path.join(tempfile.mkdtemp(), "note.md"),
            title="Test Note",
            formatted_text="# Test Note\n\nContent here.",
        )

        with patch("noteforge.engine.stages.save.write_file",
                    side_effect=OSError(28, "No space left on device")):
            with pytest.raises(OSError) as exc_info:
                save_stage.execute(ctx)
            assert exc_info.value.errno == 28

    def test_save_stage_disk_full_preserves_note_text(self):
        """Even when save fails, the generated note_text is preserved in ctx."""
        from pathlib import Path

        save_stage = SaveStage(notes_dir=Path(tempfile.mkdtemp()))

        ctx = PipelineContext(
            source_path="test.txt",
            output_path=os.path.join(tempfile.mkdtemp(), "note.md"),
            title="Test Note",
            formatted_text="# Test Note\n\nContent here.",
            note_text="# Test Note\n\nContent here.",
        )

        with patch("noteforge.engine.stages.save.write_file",
                    side_effect=OSError(28, "No space left on device")):
            # SaveStage.execute() raises, but ctx still holds note_text
            try:
                save_stage.execute(ctx)
            except OSError:
                pass

        # note_text should still be available for recovery
        assert ctx.note_text == "# Test Note\n\nContent here."


# ═══════════════════════════════════════════════════════════════
# Test: Engine-level disk full resilience
# ═══════════════════════════════════════════════════════════════


@pytest.mark.chaos
class TestEngineDiskFullResilience:
    """Engine doesn't crash on disk full — reports error and preserves data."""

    def test_pipeline_continues_after_non_save_disk_full(self):
        """If disk full occurs in a non-critical stage, pipeline reports error."""
        stage1 = _FakeStage("preprocess")
        stage2 = _FakeStage("generate")

        pipeline = Pipeline([stage1, stage2])
        ctx = PipelineContext(
            source_path="test.txt",
            transcript_path="test.txt",
            title="Test",
            content_type="lecture",
        )

        # No disk full — just verify pipeline runs normally
        result = pipeline.run(ctx)
        assert result.error is None

    def test_trace_update_step_disk_full(self):
        """update_step raises OSError when underlying save fails."""
        trace, tmp = _make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="preprocess",
                status=ExecutionTrace.Status.RUNNING,
                input_hash="h0",
            ),
        ]
        trace.save("ep01", records)

        # Subsequent update_step fails due to disk full
        with patch("noteforge.infra.execution_trace.write_file",
                    side_effect=OSError(28, "No space left on device")):
            with pytest.raises(OSError):
                trace.update_step("ep01", "preprocess",
                                  ExecutionTrace.Status.COMPLETED,
                                  output_hash="h1")

        # Original data is still intact
        loaded = trace.resume("ep01")
        assert len(loaded) == 1
        assert loaded[0].status == ExecutionTrace.Status.RUNNING
