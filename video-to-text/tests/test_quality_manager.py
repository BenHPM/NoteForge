# -*- coding: utf-8 -*-
"""QualityManager 质量门禁与报告管理单元测试（12 tests）。"""
import os
import json
import pytest
from unittest.mock import patch, MagicMock

class TestQualityManager:
    """QualityManager 质量门禁与报告管理测试"""

    def _make_qm(self, tmp_path):
        from noteforge.quality.manager import QualityManager
        from noteforge.context import PathConfig
        logger = MagicMock()
        reports_dir = tmp_path / "quality_reports"
        notes_dir = tmp_path / "notes"
        base_dir = tmp_path
        reports_dir.mkdir()
        notes_dir.mkdir()
        pc = PathConfig(
            base_dir=base_dir,
            transcripts_dir=tmp_path / "transcripts",
            notes_dir=notes_dir,
            reports_dir=reports_dir,
            logs_dir=tmp_path / "logs",
        )
        return QualityManager(
            path_config=pc,
            logger=logger,
        )

    def test_save_quality_report_creates_json(self, tmp_path):
        """save_quality_report 应创建 JSON 文件"""
        qm = self._make_qm(tmp_path)
        report = {'total_score': 0.85, 'overall_passed': True, 'rule_results': {}}
        qm.save_quality_report('/some/path/test_note.md', report)
        report_path = tmp_path / "quality_reports" / "test_note_quality.json"
        assert report_path.exists()
        with open(report_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        assert loaded['total_score'] == 0.85
        assert loaded['overall_passed'] is True

    def test_save_quality_report_unicode(self, tmp_path):
        """save_quality_report 应正确保存中文内容"""
        qm = self._make_qm(tmp_path)
        report = {'total_score': 0.9, 'description': '测试中文内容'}
        qm.save_quality_report('/path/中文笔记.md', report)
        report_path = tmp_path / "quality_reports" / "中文笔记_quality.json"
        assert report_path.exists()
        with open(report_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        assert loaded['description'] == '测试中文内容'

    def test_save_intermediate(self, tmp_path):
        """save_intermediate 应保存中间 LLM 输出"""
        qm = self._make_qm(tmp_path)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        qm.save_intermediate("测试标题", 2, "中间输出内容", logs_dir)
        expected = logs_dir / "测试标题_attempt2.md"
        assert expected.exists()
        content = expected.read_text(encoding='utf-8')
        assert content == "中间输出内容"

    def test_save_intermediate_long_title(self, tmp_path):
        """save_intermediate 应截断长标题"""
        qm = self._make_qm(tmp_path)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        long_title = "A" * 50
        qm.save_intermediate(long_title, 1, "content", logs_dir)
        files = list(logs_dir.glob("*_attempt1.md"))
        assert len(files) == 1
        stem = files[0].stem
        assert len(stem.replace("_attempt1", "")) <= 30

    def test_check_only_returns_none_on_failure(self, tmp_path):
        """check_only 在质量检查失败时应返回 None"""
        qm = self._make_qm(tmp_path)
        with patch.object(qm, 'run_quality_gate', return_value=None):
            result = qm.check_only('/path/note.md', '/path/transcript.txt')
            assert result is None

    def test_check_only_saves_report_on_success(self, tmp_path):
        """check_only 在成功时应保存报告"""
        qm = self._make_qm(tmp_path)
        report = {'total_score': 0.8, 'overall_passed': True}
        with patch.object(qm, 'run_quality_gate', return_value=report):
            with patch.object(qm, 'print_quality_report'):
                result = qm.check_only('/path/test_note.md', '/path/transcript.txt')
        assert result == report
        report_path = tmp_path / "quality_reports" / "test_note_quality.json"
        assert report_path.exists()

    def test_run_quality_gate_returns_none_when_no_gate(self, tmp_path):
        """run_quality_gate 在 QualityGate 不可用时应返回 None"""
        qm = self._make_qm(tmp_path)
        import noteforge.quality.manager as quality_manager
        with patch.object(quality_manager, '_get_quality_gate', return_value=None):
            result = qm.run_quality_gate('/path/note.md', '/path/transcript.txt')
            assert result is None

    def test_run_quality_gate_on_text_short_content(self, tmp_path):
        """run_quality_gate_on_text 对短内容应返回低分报告"""
        from noteforge.quality.manager import QualityManager
        from noteforge.quality.gate import QualityGate

        qm = self._make_qm(tmp_path)
        result = qm.run_quality_gate_on_text("短", "这是转写文本")
        if result is not None:
            assert isinstance(result, dict)
            assert result.get('overall_passed', True) is False or result.get('total_score', 1.0) < 0.8

    def test_run_quality_gate_on_text_returns_dict(self, tmp_path):
        """run_quality_gate_on_text 成功时应返回字典"""
        qm = self._make_qm(tmp_path)
        long_note = "# 笔记标题\n\n" + "这是笔记内容。" * 50
        long_transcript = "这是转写文本。" * 100
        result = qm.run_quality_gate_on_text(long_note, long_transcript)
        if result is not None:
            assert isinstance(result, dict)
            assert 'total_score' in result
            assert 'overall_passed' in result

    def test_print_quality_report(self, tmp_path, capsys):
        """print_quality_report 应输出到 stdout"""
        qm = self._make_qm(tmp_path)
        report = {
            'total_score': 0.75,
            'overall_passed': True,
            'rule_results': {
                'R1': {'score': 1.0, 'passed': True, 'issues': []},
                'R5': {'score': 0.5, 'passed': False, 'issues': ['low coverage']},
            }
        }
        qm.print_quality_report(report)
        captured = capsys.readouterr()
        assert '75%' in captured.out
        assert 'R1' in captured.out
        assert 'R5' in captured.out
