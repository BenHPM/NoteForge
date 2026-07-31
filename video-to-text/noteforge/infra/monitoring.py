# -*- coding: utf-8 -*-
"""
NoteForge PipelineMonitor — 流水线执行监控（心跳 + 停滞检测）

提供 PipelineMonitor 类，记录流水线各阶段心跳，
检测停滞（超时无心跳），写入监控日志。

存储路径: {log_dir}/monitoring.json
"""

import json
import logging
import os
from datetime import datetime
from time import monotonic
from typing import Optional, Dict, Any

from noteforge.infra.file_io import write_file

logger = logging.getLogger('noteforge.infra.monitoring')

DEFAULT_LOG_DIR = os.path.join('output', 'logs')


class PipelineMonitor:
    """Pipeline execution monitoring with heartbeat and stall detection.

    Usage:
        monitor = PipelineMonitor()
        monitor.heartbeat('generate', 'ep01')
        # ... later ...
        stall = monitor.alert_on_stall(timeout=300)
        if stall:
            logger.warning("Pipeline stalled: %s", stall)
        monitor.report_completion({'items': 10, 'errors': 0})
    """

    def __init__(self, log_dir: Optional[str] = None):
        """Initialize monitor.

        Args:
            log_dir: Directory for persistent monitoring log file.
                     Defaults to output/logs/.
        """
        self._log_dir = log_dir or DEFAULT_LOG_DIR
        self._last_heartbeat_time: Optional[float] = None
        self._last_stage: Optional[str] = None
        self._last_item_id: Optional[str] = None
        self._heartbeat_count: int = 0
        self._start_time: Optional[float] = None
        self._completion_stats: Optional[Dict[str, Any]] = None

    # ── Public API ──

    def heartbeat(self, stage: str, item_id: str) -> None:
        """Record a heartbeat for the given stage/item.

        Updates internal state with current timestamp. Call this at each
        pipeline step to signal liveness.

        Args:
            stage: Pipeline stage name (e.g. 'download', 'generate', 'evaluate').
            item_id: Item identifier (e.g. 'ep01', trace ID).
        """
        now = monotonic()
        if self._start_time is None:
            self._start_time = now

        self._last_heartbeat_time = now
        self._last_stage = stage
        self._last_item_id = item_id
        self._heartbeat_count += 1

        logger.debug(
            "Heartbeat: stage=%s item=%s (count=%d)",
            stage, item_id, self._heartbeat_count,
        )

    def alert_on_stall(self, timeout: float = 300) -> Optional[Dict[str, Any]]:
        """Check if pipeline has stalled (no heartbeat for timeout seconds).

        Args:
            timeout: Seconds without a heartbeat before considering stalled.
                     Default 300 (5 minutes).

        Returns:
            Stall info dict if stalled, None if healthy.
            Dict keys: stage, item_id, seconds_since_heartbeat, suggested_action
        """
        if self._last_heartbeat_time is None:
            # No heartbeat ever recorded — not stalled, just not started
            return None

        elapsed = monotonic() - self._last_heartbeat_time
        if elapsed < timeout:
            return None

        # Determine suggested action based on stage
        suggested_action = self._suggest_action(self._last_stage)

        stall_info = {
            'stage': self._last_stage,
            'item_id': self._last_item_id,
            'seconds_since_heartbeat': round(elapsed, 1),
            'suggested_action': suggested_action,
        }

        logger.warning(
            "Pipeline stall detected: stage=%s item=%s elapsed=%.1fs",
            self._last_stage, self._last_item_id, elapsed,
        )

        return stall_info

    def report_completion(self, stats: Dict[str, Any]) -> None:
        """Record pipeline completion statistics. Writes to monitoring log file.

        Args:
            stats: Completion statistics dict. Typical keys:
                - items: number of items processed
                - errors: number of errors
                - total_duration_seconds: total pipeline duration
                - quality_pass_rate: fraction of items passing quality gate
        """
        self._completion_stats = stats

        # Add monitoring metadata
        record = {
            'timestamp': datetime.now().isoformat(),
            'heartbeat_count': self._heartbeat_count,
            'last_stage': self._last_stage,
            'last_item_id': self._last_item_id,
            'stats': stats,
        }

        if self._start_time is not None:
            record['total_duration_seconds'] = round(monotonic() - self._start_time, 2)

        # Persist to log file
        self._write_monitoring_log(record)

        logger.info(
            "Pipeline completed: %d heartbeats, stats=%s",
            self._heartbeat_count, stats,
        )

    def get_status(self) -> Dict[str, Any]:
        """Get current monitoring status.

        Returns:
            Dict with last_heartbeat, last_stage, last_item_id,
            heartbeat_count, and seconds_since_last_heartbeat (if any).
        """
        status: Dict[str, Any] = {
            'last_stage': self._last_stage,
            'last_item_id': self._last_item_id,
            'heartbeat_count': self._heartbeat_count,
        }

        if self._last_heartbeat_time is not None:
            status['seconds_since_last_heartbeat'] = round(
                monotonic() - self._last_heartbeat_time, 2
            )
        else:
            status['seconds_since_last_heartbeat'] = None

        if self._start_time is not None:
            status['total_elapsed_seconds'] = round(
                monotonic() - self._start_time, 2
            )

        return status

    def reset(self) -> None:
        """Reset monitoring state (e.g., at start of new pipeline run)."""
        self._last_heartbeat_time = None
        self._last_stage = None
        self._last_item_id = None
        self._heartbeat_count = 0
        self._start_time = None
        self._completion_stats = None

        logger.debug("PipelineMonitor reset")

    # ── Internal ──

    def _suggest_action(self, stage: Optional[str]) -> str:
        """Suggest recovery action based on the stalled stage."""
        if stage is None:
            return "Check pipeline logs and restart with --resume"

        stage_actions = {
            'download': "Check network connectivity; retry with --resume",
            'transcribe': "Check ASR environment (envs/paraformer); consider --health-check-asr",
            'preprocess': "Check input transcript integrity; retry with --resume",
            'generate': "Check LLM API connectivity; check circuit breaker state; retry with --resume",
            'format': "Check note output; usually transient; retry with --resume",
            'evaluate': "Check quality gate config; retry with --resume",
            'sync': "Check Feishu auth (lark-cli); retry sync separately",
        }

        return stage_actions.get(stage, "Check logs and retry with --resume")

    def _write_monitoring_log(self, record: Dict[str, Any]) -> None:
        """Write monitoring record to log file (append JSON lines)."""
        os.makedirs(self._log_dir, exist_ok=True)
        log_path = os.path.join(self._log_dir, 'monitoring.json')

        # Read existing records
        records = []
        if os.path.exists(log_path):
            try:
                with open(log_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                if content:
                    records = json.loads(content)
                    if not isinstance(records, list):
                        records = [records]
            except (json.JSONDecodeError, OSError):
                records = []

        records.append(record)

        # Write back atomically
        content = json.dumps(records, ensure_ascii=False, indent=2)
        write_file(log_path, content)
