# -*- coding: utf-8 -*-
"""
Integration test: Checkpoint recovery with ExecutionTrace

Tests the full chain:
  ExecutionTrace with mixed COMPLETED/PENDING stages
  → resume() returns correct state
  → get_last_completed_stage() returns right stage
  → Corrupted checkpoint handled gracefully
  → Hash chain validation on resume

Uses real file I/O (tempdir) with no external services.
"""

import json
import os
import tempfile

import pytest

from noteforge.infra.execution_trace import ExecutionTrace


pytestmark = pytest.mark.integration


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _make_trace():
    """Create an ExecutionTrace with a temporary directory."""
    tmp = tempfile.mkdtemp()
    return ExecutionTrace(trace_dir=tmp), tmp


def _completed_step(stage, input_hash, output_hash):
    """Create a COMPLETED StepRecord."""
    return ExecutionTrace.StepRecord(
        stage=stage,
        status=ExecutionTrace.Status.COMPLETED,
        input_hash=input_hash,
        output_hash=output_hash,
        started_at="2026-08-01T10:00:00",
        completed_at="2026-08-01T10:05:00",
    )


def _pending_step(stage, input_hash):
    """Create a PENDING StepRecord."""
    return ExecutionTrace.StepRecord(
        stage=stage,
        status=ExecutionTrace.Status.PENDING,
        input_hash=input_hash,
    )


def _running_step(stage, input_hash):
    """Create a RUNNING StepRecord."""
    return ExecutionTrace.StepRecord(
        stage=stage,
        status=ExecutionTrace.Status.RUNNING,
        input_hash=input_hash,
        started_at="2026-08-01T10:05:00",
    )


def _failed_step(stage, input_hash, error_type="TRANSIENT"):
    """Create a FAILED StepRecord."""
    return ExecutionTrace.StepRecord(
        stage=stage,
        status=ExecutionTrace.Status.FAILED,
        input_hash=input_hash,
        error_type=error_type,
        retry_count=1,
    )


# ═══════════════════════════════════════════════════════════════
# resume() returns correct state
# ═══════════════════════════════════════════════════════════════


class TestResumeReturnsCorrectState:
    """ExecutionTrace with mixed COMPLETED/PENDING stages: resume() returns correct state."""

    def test_mixed_completed_and_pending(self):
        """resume() returns records with correct statuses for mixed pipeline."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
            _completed_step("transcribe", "h2", "h3"),
            _pending_step("generate", "h3"),
            _pending_step("format", "h4"),
            _pending_step("evaluate", "h5"),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        assert len(loaded) == 5
        assert loaded[0].status == ExecutionTrace.Status.COMPLETED
        assert loaded[1].status == ExecutionTrace.Status.COMPLETED
        assert loaded[2].status == ExecutionTrace.Status.PENDING
        assert loaded[3].status == ExecutionTrace.Status.PENDING
        assert loaded[4].status == ExecutionTrace.Status.PENDING

    def test_completed_failed_pending_mix(self):
        """resume() preserves COMPLETED, FAILED, and PENDING statuses."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
            _failed_step("transcribe", "h2", "TRANSIENT"),
            _pending_step("generate", "h3"),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        assert loaded[0].status == ExecutionTrace.Status.COMPLETED
        assert loaded[1].status == ExecutionTrace.Status.FAILED
        assert loaded[2].status == ExecutionTrace.Status.PENDING

    def test_all_completed_pipeline(self):
        """resume() returns all COMPLETED for a fully completed pipeline."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
            _completed_step("transcribe", "h2", "h3"),
            _completed_step("generate", "h3", "h4"),
            _completed_step("format", "h4", "h5"),
            _completed_step("evaluate", "h5", "h6"),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        assert all(r.status == ExecutionTrace.Status.COMPLETED for r in loaded)

    def test_all_pending_pipeline(self):
        """resume() returns all PENDING for a fresh pipeline."""
        trace, tmp = _make_trace()
        records = [
            _pending_step("download", "h1"),
            _pending_step("transcribe", "h2"),
            _pending_step("generate", "h3"),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        assert all(r.status == ExecutionTrace.Status.PENDING for r in loaded)

    def test_running_step_preserved(self):
        """resume() preserves RUNNING status for in-progress steps."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
            _running_step("transcribe", "h2"),
            _pending_step("generate", "h3"),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        assert loaded[0].status == ExecutionTrace.Status.COMPLETED
        assert loaded[1].status == ExecutionTrace.Status.RUNNING
        assert loaded[2].status == ExecutionTrace.Status.PENDING


# ═══════════════════════════════════════════════════════════════
# get_last_completed_stage() returns right stage
# ═══════════════════════════════════════════════════════════════


class TestGetLastCompletedStage:
    """get_last_completed_stage() returns the correct stage for checkpoint recovery."""

    def test_returns_last_completed_in_mixed_pipeline(self):
        """Returns 'transcribe' when download and transcribe are COMPLETED."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
            _completed_step("transcribe", "h2", "h3"),
            _pending_step("generate", "h3"),
        ]
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") == "transcribe"

    def test_returns_early_stage_when_only_first_completed(self):
        """Returns 'download' when only download is COMPLETED."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
            _failed_step("transcribe", "h2"),
            _pending_step("generate", "h3"),
        ]
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") == "download"

    def test_returns_none_when_nothing_completed(self):
        """Returns None when no stage is COMPLETED."""
        trace, tmp = _make_trace()
        records = [
            _pending_step("download", "h1"),
            _pending_step("transcribe", "h2"),
        ]
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") is None

    def test_returns_last_of_multiple_completed(self):
        """Returns the last COMPLETED stage when multiple are done."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
            _completed_step("transcribe", "h2", "h3"),
            _completed_step("generate", "h3", "h4"),
            _pending_step("format", "h4"),
        ]
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") == "generate"

    def test_returns_none_for_nonexistent_trace(self):
        """Returns None for a trace that does not exist."""
        trace, tmp = _make_trace()
        assert trace.get_last_completed_stage("nonexistent") is None


# ═══════════════════════════════════════════════════════════════
# Corrupted checkpoint handled gracefully
# ═══════════════════════════════════════════════════════════════


class TestCorruptedCheckpointHandling:
    """Corrupted checkpoint files are handled gracefully."""

    def test_truncated_json_returns_empty(self):
        """Truncated JSON file returns empty list (no crash)."""
        trace, tmp = _make_trace()
        path = os.path.join(tmp, "ep01.json")
        with open(path, 'w', encoding='utf-8') as f:
            f.write('{"steps": [{"stage": "download", "status": "comp')
        loaded = trace.resume("ep01")
        assert loaded == []

    def test_empty_file_returns_empty(self):
        """Empty file returns empty list."""
        trace, tmp = _make_trace()
        path = os.path.join(tmp, "ep01.json")
        with open(path, 'w', encoding='utf-8') as f:
            f.write('')
        loaded = trace.resume("ep01")
        assert loaded == []

    def test_garbage_json_returns_empty(self):
        """Garbage content returns empty list."""
        trace, tmp = _make_trace()
        path = os.path.join(tmp, "ep01.json")
        with open(path, 'w', encoding='utf-8') as f:
            f.write('<<<not json>>>')
        loaded = trace.resume("ep01")
        assert loaded == []

    def test_wrong_structure_returns_empty(self):
        """JSON with wrong structure (e.g. string) returns empty list."""
        trace, tmp = _make_trace()
        path = os.path.join(tmp, "ep01.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump("just a string", f)
        loaded = trace.resume("ep01")
        assert loaded == []

    def test_partial_corrupt_records_skipped(self):
        """Partially corrupt records are skipped; valid ones preserved."""
        trace, tmp = _make_trace()
        path = os.path.join(tmp, "ep01.json")
        data = {
            "steps": [
                {"stage": "download", "status": "completed", "input_hash": "h1"},
                {"bad": "record"},  # missing required fields
                {"stage": "generate", "status": "pending", "input_hash": "h2"},
            ]
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        loaded = trace.resume("ep01")
        assert len(loaded) == 2
        assert loaded[0].stage == "download"
        assert loaded[1].stage == "generate"

    def test_get_last_completed_stage_on_corrupt_file(self):
        """get_last_completed_stage returns None for corrupt file."""
        trace, tmp = _make_trace()
        path = os.path.join(tmp, "ep01.json")
        with open(path, 'w', encoding='utf-8') as f:
            f.write('corrupted')
        assert trace.get_last_completed_stage("ep01") is None

    def test_update_step_on_corrupt_file_creates_new(self):
        """update_step on a corrupt file creates a fresh record."""
        trace, tmp = _make_trace()
        path = os.path.join(tmp, "ep01.json")
        with open(path, 'w', encoding='utf-8') as f:
            f.write('corrupted')

        trace.update_step("ep01", "download", ExecutionTrace.Status.RUNNING,
                          input_hash="h1")
        loaded = trace.resume("ep01")
        assert len(loaded) == 1
        assert loaded[0].stage == "download"
        assert loaded[0].status == ExecutionTrace.Status.RUNNING


# ═══════════════════════════════════════════════════════════════
# Hash chain validation on resume
# ═══════════════════════════════════════════════════════════════


class TestHashChainValidationOnResume:
    """Hash chain validation detects tampering and invalidates downstream steps."""

    def test_valid_chain_preserves_all_statuses(self):
        """Valid hash chain: all COMPLETED statuses preserved on resume."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
            _completed_step("transcribe", "h2", "h3"),
            _completed_step("generate", "h3", "h4"),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        assert all(r.status == ExecutionTrace.Status.COMPLETED for r in loaded)

    def test_broken_chain_invalidates_from_mismatch(self):
        """Broken hash chain: steps from mismatch onward are marked FAILED."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
            ExecutionTrace.StepRecord(
                stage="transcribe",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="MISMATCH",  # does not match download.output_hash
                output_hash="h3",
                started_at="2026-08-01T10:05:00",
                completed_at="2026-08-01T10:15:00",
            ),
            _completed_step("generate", "h3", "h4"),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        # download still COMPLETED
        assert loaded[0].status == ExecutionTrace.Status.COMPLETED
        # transcribe and generate invalidated
        assert loaded[1].status == ExecutionTrace.Status.FAILED
        assert loaded[1].output_hash is None
        assert loaded[2].status == ExecutionTrace.Status.FAILED
        assert loaded[2].output_hash is None

    def test_chain_break_mid_pipeline(self):
        """Hash chain break in the middle invalidates only downstream steps."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
            _completed_step("transcribe", "h2", "h3"),
            ExecutionTrace.StepRecord(
                stage="generate",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="TAMPERED",  # does not match transcribe.output_hash
                output_hash="h4",
                started_at="2026-08-01T10:15:00",
                completed_at="2026-08-01T10:30:00",
            ),
            _completed_step("format", "h4", "h5"),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        # download and transcribe still COMPLETED
        assert loaded[0].status == ExecutionTrace.Status.COMPLETED
        assert loaded[1].status == ExecutionTrace.Status.COMPLETED
        # generate and format invalidated
        assert loaded[2].status == ExecutionTrace.Status.FAILED
        assert loaded[3].status == ExecutionTrace.Status.FAILED

    def test_chain_validation_auto_saves_corrected_file(self):
        """After hash chain break, the corrected file is auto-saved."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
            ExecutionTrace.StepRecord(
                stage="transcribe",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="MISMATCH",
                output_hash="h3",
                started_at="2026-08-01T10:05:00",
                completed_at="2026-08-01T10:15:00",
            ),
        ]
        trace.save("ep01", records)

        # First resume detects and corrects
        loaded1 = trace.resume("ep01")
        assert loaded1[1].status == ExecutionTrace.Status.FAILED

        # Second resume should load the corrected file
        loaded2 = trace.resume("ep01")
        assert loaded2[1].status == ExecutionTrace.Status.FAILED

    def test_config_hash_preserved_across_resume(self):
        """config_hash is preserved when saving and resuming."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
        ]
        trace.save("ep01", records, config_hash="abc123")

        loaded = trace.resume("ep01")
        assert trace._last_config_hash == "abc123"

    def test_config_hash_detects_change(self):
        """get_config_hash returns the stored hash for comparison."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
        ]
        trace.save("ep01", records, config_hash="original_hash")

        stored_hash = trace.get_config_hash("ep01")
        assert stored_hash == "original_hash"

        # Simulate config change by saving with different hash
        trace.save("ep01", records, config_hash="changed_hash")
        new_hash = trace.get_config_hash("ep01")
        assert new_hash == "changed_hash"
        assert stored_hash != new_hash

    def test_dead_letter_stops_chain_validation(self):
        """DEAD_LETTER records stop hash chain validation (no cascade past them)."""
        trace, tmp = _make_trace()
        records = [
            _completed_step("download", "h1", "h2"),
            ExecutionTrace.StepRecord(
                stage="transcribe",
                status=ExecutionTrace.Status.DEAD_LETTER,
                input_hash="h2",
                error_type="PERMANENT",
                completed_at="2026-08-01T10:15:00",
            ),
            # These would normally be invalidated by chain break,
            # but DEAD_LETTER stops validation
            _completed_step("generate", "h3", "h4"),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        # generate stays COMPLETED because DEAD_LETTER stopped validation
        assert loaded[2].status == ExecutionTrace.Status.COMPLETED
