# -*- coding: utf-8 -*-
"""
QualityTrend 单元测试

覆盖:
  - 初始化（新文件 / 已有文件 / 损坏文件）
  - record 追加写入
  - get_records 过滤（domain / since / min_score）
  - get_stats 统计摘要
  - clear 清空
  - 与 QualityManager 集成
"""

import json
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from noteforge.quality.trend import QualityTrend
from noteforge.quality.manager import QualityManager
from noteforge.context import PathConfig


# ================================================================
# Helpers
# ================================================================

def make_trend(tmp_dir=None):
    if tmp_dir is None:
        tmp_dir = tempfile.mkdtemp()
    return QualityTrend(log_dir=tmp_dir)


def sample_report(score=0.85, passed=True):
    return {
        "total_score": score,
        "overall_passed": passed,
        "rule_results": {
            "R1": {"score": 1.0, "passed": True, "issue_count": 0, "issues": []},
            "R2": {"score": 0.9, "passed": True, "issue_count": 0, "issues": []},
            "R3": {"score": 0.8, "passed": True, "issue_count": 0, "issues": []},
        },
        "summary": "Test report",
    }


def make_pc(tmp_dir):
    """Minimal PathConfig for QualityManager"""
    base = Path(tmp_dir)
    for sub in ["transcripts", "notes", "reports", "logs"]:
        (base / sub).mkdir(parents=True, exist_ok=True)
    return PathConfig(
        base_dir=base,
        transcripts_dir=base / "transcripts",
        notes_dir=base / "notes",
        reports_dir=base / "reports",
        logs_dir=base / "logs",
    )


# ================================================================
# QualityTrend tests
# ================================================================

class TestQualityTrendInit:

    def test_new_file_creates_empty(self, tmp_path):
        trend = make_trend(str(tmp_path))
        assert trend.record_count == 0

    def test_existing_file_loads(self, tmp_path):
        data = [{"timestamp": "2025-01-01T00:00:00", "total_score": 0.8}]
        (tmp_path / "quality_trend.json").write_text(
            json.dumps(data), encoding='utf-8'
        )
        trend = make_trend(str(tmp_path))
        assert trend.record_count == 1

    def test_corrupt_file_returns_empty(self, tmp_path):
        (tmp_path / "quality_trend.json").write_text("not json{", encoding='utf-8')
        trend = make_trend(str(tmp_path))
        assert trend.record_count == 0


class TestQualityTrendRecord:

    def test_record_appends(self, tmp_path):
        trend = make_trend(str(tmp_path))
        trend.record("/notes/ep01.md", sample_report(), domain="geopolitics")
        assert trend.record_count == 1

    def test_record_multiple(self, tmp_path):
        trend = make_trend(str(tmp_path))
        for i in range(5):
            trend.record(f"/notes/ep{i:02d}.md", sample_report(score=0.7 + i * 0.05))
        assert trend.record_count == 5

    def test_record_persists_to_disk(self, tmp_path):
        trend = make_trend(str(tmp_path))
        trend.record("/notes/ep01.md", sample_report())
        # reload
        trend2 = make_trend(str(tmp_path))
        assert trend2.record_count == 1
        data = json.loads((tmp_path / "quality_trend.json").read_text(encoding='utf-8'))
        assert len(data) == 1
        assert data[0]["note_stem"] == "ep01"

    def test_record_includes_llm_eval(self, tmp_path):
        report = sample_report()
        report["llm_eval"] = {
            "richness_score": 4.0,
            "readability_score": 3.5,
            "faithfulness_score": 4.5,
            "actionability_score": 3.0,
            "overall_score": 3.8,
            "feedback": "Good",
            "suggestions": ["Add more context"],
        }
        trend = make_trend(str(tmp_path))
        trend.record("/notes/ep01.md", report, domain="finance_investment")
        data = json.loads((tmp_path / "quality_trend.json").read_text(encoding='utf-8'))
        assert "llm_eval" in data[0]
        assert data[0]["llm_eval"]["overall_score"] == 3.8


class TestQualityTrendQuery:

    def test_get_records_all(self, tmp_path):
        trend = make_trend(str(tmp_path))
        for i in range(3):
            trend.record(f"/notes/ep{i}.md", sample_report())
        assert len(trend.get_records()) == 3

    def test_filter_by_domain(self, tmp_path):
        trend = make_trend(str(tmp_path))
        trend.record("/notes/ep01.md", sample_report(), domain="geopolitics")
        trend.record("/notes/ep02.md", sample_report(), domain="finance")
        trend.record("/notes/ep03.md", sample_report(), domain="geopolitics")
        assert len(trend.get_records(domain="geopolitics")) == 2
        assert len(trend.get_records(domain="finance")) == 1
        assert len(trend.get_records(domain="nonexistent")) == 0

    def test_filter_by_min_score(self, tmp_path):
        trend = make_trend(str(tmp_path))
        trend.record("/notes/ep01.md", sample_report(score=0.9))
        trend.record("/notes/ep02.md", sample_report(score=0.5))
        trend.record("/notes/ep03.md", sample_report(score=0.3))
        assert len(trend.get_records(min_score=0.6)) == 1
        assert len(trend.get_records(min_score=0.0)) == 3


class TestQualityTrendStats:

    def test_stats_basic(self, tmp_path):
        trend = make_trend(str(tmp_path))
        trend.record("/notes/ep01.md", sample_report(score=0.8, passed=True))
        trend.record("/notes/ep02.md", sample_report(score=0.9, passed=True))
        trend.record("/notes/ep03.md", sample_report(score=0.4, passed=False))
        stats = trend.get_stats()
        assert stats["total"] == 3
        assert abs(stats["avg_score"] - 0.7) < 0.01
        assert abs(stats["pass_rate"] - 2/3) < 0.01
        assert abs(stats["recent_5_avg"] - 0.7) < 0.01
        assert abs(stats["min_score"] - 0.4) < 0.01
        assert abs(stats["max_score"] - 0.9) < 0.01

    def test_stats_empty(self, tmp_path):
        trend = make_trend(str(tmp_path))
        stats = trend.get_stats()
        assert stats["total"] == 0

    def test_stats_by_domain(self, tmp_path):
        trend = make_trend(str(tmp_path))
        trend.record("/notes/ep01.md", sample_report(score=0.9), domain="geopolitics")
        trend.record("/notes/ep02.md", sample_report(score=0.8), domain="geopolitics")
        trend.record("/notes/ep03.md", sample_report(score=0.5), domain="finance")
        stats = trend.get_stats()
        assert abs(stats["domain_scores"]["geopolitics"] - 0.85) < 0.01
        assert abs(stats["domain_scores"]["finance"] - 0.5) < 0.01

    def test_stats_filtered_by_domain(self, tmp_path):
        trend = make_trend(str(tmp_path))
        trend.record("/notes/ep01.md", sample_report(score=0.9), domain="geopolitics")
        trend.record("/notes/ep02.md", sample_report(score=0.5), domain="finance")
        stats = trend.get_stats(domain="geopolitics")
        assert stats["total"] == 1
        assert abs(stats["avg_score"] - 0.9) < 0.01


class TestQualityTrendClear:

    def test_clear(self, tmp_path):
        trend = make_trend(str(tmp_path))
        trend.record("/notes/ep01.md", sample_report())
        assert trend.record_count == 1
        trend.clear()
        assert trend.record_count == 0
        # reload confirms empty
        trend2 = make_trend(str(tmp_path))
        assert trend2.record_count == 0


class TestQualityTrendIntegration:

    def test_quality_manager_records_trend(self, tmp_path):
        """QualityManager.check_only 自动记录趋势"""
        trend = make_trend(str(tmp_path))
        pc = make_pc(tmp_path)
        logger = MagicMock()
        qm = QualityManager(path_config=pc, logger=logger, config={}, content_type='lecture')
        qm.set_trend(trend)

        # 模拟 run_quality_gate 返回报告
        report = sample_report(score=0.85)
        with patch.object(qm, 'run_quality_gate', return_value=report):
            result = qm.check_only("/notes/ep01.md", "/transcripts/ep01.txt")

        assert result is not None
        assert trend.record_count == 1
        entry = trend.get_records()[0]
        assert entry["total_score"] == 0.85
        assert entry["overall_passed"] is True
        assert entry["domain"] == "general"  # ep01 匹配 general

    def test_quality_manager_no_trend_skips(self, tmp_path):
        """未设置 trend 时不应报错"""
        pc = make_pc(tmp_path)
        logger = MagicMock()
        qm = QualityManager(path_config=pc, logger=logger, config={})
        # 未 set_trend
        report = sample_report()
        with patch.object(qm, 'run_quality_gate', return_value=report):
            result = qm.check_only("/notes/ep01.md", "/transcripts/ep01.txt")
        assert result is not None
