# -*- coding: utf-8 -*-
"""Tests for PipelineMonitor"""

import json
import os
import tempfile
from time import monotonic
from unittest.mock import patch

import pytest

from noteforge.infra.monitoring import PipelineMonitor


# ═══════════════════════════════════════════════════════════════
# Heartbeat tests
# ═══════════════════════════════════════════════════════════════


class TestHeartbeat:
    """PipelineMonitor.heartbeat records correctly."""

    def test_heartbeat_records_stage_and_item(self):
        monitor = PipelineMonitor()
        monitor.heartbeat('generate', 'ep01')
        assert monitor._last_stage == 'generate'
        assert monitor._last_item_id == 'ep01'

    def test_heartbeat_increments_count(self):
        monitor = PipelineMonitor()
        assert monitor._heartbeat_count == 0
        monitor.heartbeat('download', 'ep01')
        assert monitor._heartbeat_count == 1
        monitor.heartbeat('transcribe', 'ep01')
        assert monitor._heartbeat_count == 2

    def test_heartbeat_sets_start_time_on_first_call(self):
        monitor = PipelineMonitor()
        assert monitor._start_time is None
        monitor.heartbeat('download', 'ep01')
        assert monitor._start_time is not None

    def test_heartbeat_does_not_change_start_time_on_subsequent_calls(self):
        monitor = PipelineMonitor()
        monitor.heartbeat('download', 'ep01')
        first_start = monitor._start_time
        monitor.heartbeat('generate', 'ep01')
        assert monitor._start_time == first_start

    def test_multiple_heartbeats_update_last_heartbeat(self):
        monitor = PipelineMonitor()
        monitor.heartbeat('download', 'ep01')
        first_time = monitor._last_heartbeat_time
        # Advance mock time
        with patch('noteforge.infra.monitoring.monotonic', return_value=first_time + 10):
            monitor.heartbeat('generate', 'ep02')
        assert monitor._last_stage == 'generate'
        assert monitor._last_item_id == 'ep02'
        assert monitor._last_heartbeat_time == first_time + 10


# ═══════════════════════════════════════════════════════════════
# Stall detection tests
# ═══════════════════════════════════════════════════════════════


class TestAlertOnStall:
    """PipelineMonitor.alert_on_stall detects stall and returns None when healthy."""

    def test_returns_none_when_no_heartbeat(self):
        monitor = PipelineMonitor()
        result = monitor.alert_on_stall(timeout=300)
        assert result is None

    def test_returns_none_when_heartbeat_recent(self):
        monitor = PipelineMonitor()
        now = monotonic()
        with patch('noteforge.infra.monitoring.monotonic', return_value=now):
            monitor.heartbeat('generate', 'ep01')
        # Check immediately — should be healthy
        with patch('noteforge.infra.monitoring.monotonic', return_value=now + 10):
            result = monitor.alert_on_stall(timeout=300)
        assert result is None

    def test_detects_stall_after_timeout(self):
        monitor = PipelineMonitor()
        now = monotonic()
        with patch('noteforge.infra.monitoring.monotonic', return_value=now):
            monitor.heartbeat('generate', 'ep01')
        # Advance time past timeout
        with patch('noteforge.infra.monitoring.monotonic', return_value=now + 600):
            result = monitor.alert_on_stall(timeout=300)
        assert result is not None
        assert result['stage'] == 'generate'
        assert result['item_id'] == 'ep01'
        assert result['seconds_since_heartbeat'] == 600.0
        assert 'suggested_action' in result

    def test_stall_info_has_suggested_action(self):
        monitor = PipelineMonitor()
        now = monotonic()
        with patch('noteforge.infra.monitoring.monotonic', return_value=now):
            monitor.heartbeat('download', 'ep01')
        with patch('noteforge.infra.monitoring.monotonic', return_value=now + 600):
            result = monitor.alert_on_stall(timeout=300)
        assert 'network' in result['suggested_action'].lower() or 'resume' in result['suggested_action'].lower()

    def test_stall_at_generate_stage_suggests_llm_check(self):
        monitor = PipelineMonitor()
        now = monotonic()
        with patch('noteforge.infra.monitoring.monotonic', return_value=now):
            monitor.heartbeat('generate', 'ep01')
        with patch('noteforge.infra.monitoring.monotonic', return_value=now + 600):
            result = monitor.alert_on_stall(timeout=300)
        assert 'LLM' in result['suggested_action'] or 'circuit' in result['suggested_action'].lower()

    def test_stall_at_transcribe_stage_suggests_asr_check(self):
        monitor = PipelineMonitor()
        now = monotonic()
        with patch('noteforge.infra.monitoring.monotonic', return_value=now):
            monitor.heartbeat('transcribe', 'ep01')
        with patch('noteforge.infra.monitoring.monotonic', return_value=now + 600):
            result = monitor.alert_on_stall(timeout=300)
        assert 'ASR' in result['suggested_action'] or 'health-check' in result['suggested_action']

    def test_custom_timeout(self):
        monitor = PipelineMonitor()
        now = monotonic()
        with patch('noteforge.infra.monitoring.monotonic', return_value=now):
            monitor.heartbeat('generate', 'ep01')
        # 60s elapsed, timeout=30 → stalled
        with patch('noteforge.infra.monitoring.monotonic', return_value=now + 60):
            result = monitor.alert_on_stall(timeout=30)
        assert result is not None
        # 60s elapsed, timeout=120 → not stalled
        with patch('noteforge.infra.monitoring.monotonic', return_value=now + 60):
            result = monitor.alert_on_stall(timeout=120)
        assert result is None


# ═══════════════════════════════════════════════════════════════
# Report completion tests
# ═══════════════════════════════════════════════════════════════


class TestReportCompletion:
    """PipelineMonitor.report_completion writes stats."""

    def test_writes_monitoring_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            monitor = PipelineMonitor(log_dir=tmpdir)
            monitor.heartbeat('generate', 'ep01')
            monitor.report_completion({'items': 5, 'errors': 0})

            log_path = os.path.join(tmpdir, 'monitoring.json')
            assert os.path.exists(log_path)

            with open(log_path, 'r', encoding='utf-8') as f:
                records = json.load(f)
            assert isinstance(records, list)
            assert len(records) == 1
            assert records[0]['stats']['items'] == 5
            assert records[0]['stats']['errors'] == 0

    def test_log_contains_heartbeat_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            monitor = PipelineMonitor(log_dir=tmpdir)
            monitor.heartbeat('download', 'ep01')
            monitor.heartbeat('generate', 'ep01')
            monitor.report_completion({'items': 1})

            log_path = os.path.join(tmpdir, 'monitoring.json')
            with open(log_path, 'r', encoding='utf-8') as f:
                records = json.load(f)
            assert records[0]['heartbeat_count'] == 2

    def test_log_contains_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            monitor = PipelineMonitor(log_dir=tmpdir)
            monitor.heartbeat('generate', 'ep01')
            monitor.report_completion({'items': 1})

            log_path = os.path.join(tmpdir, 'monitoring.json')
            with open(log_path, 'r', encoding='utf-8') as f:
                records = json.load(f)
            assert 'timestamp' in records[0]

    def test_log_contains_duration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            monitor = PipelineMonitor(log_dir=tmpdir)
            now = monotonic()
            with patch('noteforge.infra.monitoring.monotonic', return_value=now):
                monitor.heartbeat('generate', 'ep01')
            with patch('noteforge.infra.monitoring.monotonic', return_value=now + 42.5):
                monitor.report_completion({'items': 1})

            log_path = os.path.join(tmpdir, 'monitoring.json')
            with open(log_path, 'r', encoding='utf-8') as f:
                records = json.load(f)
            assert records[0]['total_duration_seconds'] == 42.5

    def test_appends_to_existing_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            monitor = PipelineMonitor(log_dir=tmpdir)
            monitor.heartbeat('generate', 'ep01')
            monitor.report_completion({'items': 1})

            # Second run
            monitor.reset()
            monitor.heartbeat('generate', 'ep02')
            monitor.report_completion({'items': 1})

            log_path = os.path.join(tmpdir, 'monitoring.json')
            with open(log_path, 'r', encoding='utf-8') as f:
                records = json.load(f)
            assert len(records) == 2


# ═══════════════════════════════════════════════════════════════
# Get status tests
# ═══════════════════════════════════════════════════════════════


class TestGetStatus:
    """PipelineMonitor.get_status returns current state."""

    def test_initial_status(self):
        monitor = PipelineMonitor()
        status = monitor.get_status()
        assert status['last_stage'] is None
        assert status['last_item_id'] is None
        assert status['heartbeat_count'] == 0
        assert status['seconds_since_last_heartbeat'] is None

    def test_status_after_heartbeat(self):
        monitor = PipelineMonitor()
        now = monotonic()
        with patch('noteforge.infra.monitoring.monotonic', return_value=now):
            monitor.heartbeat('generate', 'ep01')
        with patch('noteforge.infra.monitoring.monotonic', return_value=now + 5):
            status = monitor.get_status()
        assert status['last_stage'] == 'generate'
        assert status['last_item_id'] == 'ep01'
        assert status['heartbeat_count'] == 1
        assert status['seconds_since_last_heartbeat'] == 5.0

    def test_status_includes_elapsed_time(self):
        monitor = PipelineMonitor()
        now = monotonic()
        with patch('noteforge.infra.monitoring.monotonic', return_value=now):
            monitor.heartbeat('generate', 'ep01')
        with patch('noteforge.infra.monitoring.monotonic', return_value=now + 100):
            status = monitor.get_status()
        assert status['total_elapsed_seconds'] == 100.0


# ═══════════════════════════════════════════════════════════════
# Reset tests
# ═══════════════════════════════════════════════════════════════


class TestReset:
    """PipelineMonitor.reset clears state."""

    def test_reset_clears_heartbeat(self):
        monitor = PipelineMonitor()
        monitor.heartbeat('generate', 'ep01')
        assert monitor._last_heartbeat_time is not None
        monitor.reset()
        assert monitor._last_heartbeat_time is None

    def test_reset_clears_stage_and_item(self):
        monitor = PipelineMonitor()
        monitor.heartbeat('generate', 'ep01')
        monitor.reset()
        assert monitor._last_stage is None
        assert monitor._last_item_id is None

    def test_reset_clears_count(self):
        monitor = PipelineMonitor()
        monitor.heartbeat('generate', 'ep01')
        monitor.heartbeat('generate', 'ep02')
        assert monitor._heartbeat_count == 2
        monitor.reset()
        assert monitor._heartbeat_count == 0

    def test_reset_clears_start_time(self):
        monitor = PipelineMonitor()
        monitor.heartbeat('generate', 'ep01')
        assert monitor._start_time is not None
        monitor.reset()
        assert monitor._start_time is None

    def test_reset_clears_completion_stats(self):
        monitor = PipelineMonitor()
        monitor._completion_stats = {'items': 5}
        monitor.reset()
        assert monitor._completion_stats is None

    def test_status_after_reset_matches_initial(self):
        monitor = PipelineMonitor()
        monitor.heartbeat('generate', 'ep01')
        monitor.reset()
        status = monitor.get_status()
        assert status['last_stage'] is None
        assert status['last_item_id'] is None
        assert status['heartbeat_count'] == 0
        assert status['seconds_since_last_heartbeat'] is None
