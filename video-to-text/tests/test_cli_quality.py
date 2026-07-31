# -*- coding: utf-8 -*-
"""测试 CLI 质量命令: quality_view, quality_list, check --format/--verbose"""

import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from noteforge.cli.commands.quality_view import (
    run_quality_view,
    run_quality_list,
    _format_table,
    _format_json,
    _format_md,
    _load_report_json,
    _find_reports_dir,
    _RULE_NAMES,
)
from noteforge.cli.commands.check import (
    run_check_only,
    _format_table as check_format_table,
    _report_to_quality_report,
)


# ---- Fixtures ----

SAMPLE_REPORT = {
    "note_label": "test_note",
    "source_label": "test_source",
    "total_score": 0.75,
    "overall_passed": False,
    "rule_results": {
        "R0": {"score": 1.0, "passed": True, "issue_count": 0, "issues": []},
        "R1": {"score": 1.0, "passed": True, "issue_count": 0, "issues": []},
        "R2": {"score": 1.0, "passed": True, "issue_count": 0, "issues": []},
        "R3": {
            "score": 0.0, "passed": False, "issue_count": 2,
            "issues": [
                {"severity": "medium", "line_range": "L6",
                 "description": "疑似语义反转: '稳定'",
                 "suggestion": "请人工核实"},
                {"severity": "medium", "line_range": "L10",
                 "description": "疑似语义反转: '增长'",
                 "suggestion": "请人工核实"},
            ]
        },
        "R5": {"score": 0.6, "passed": False, "issue_count": 1,
               "issues": [
                   {"severity": "major", "line_range": "L1-50",
                    "description": "覆盖度不足",
                    "suggestion": "补充遗漏内容"}
               ]},
    },
    "summary": "R3 和 R5 未通过",
    "metrics": {
        "compression_ratio": 0.22,
        "structure_score": 0.85,
        "info_density": 0.90,
        "readability_score": 0.78,
        "quote_ratio": 0.12,
        "action_specificity": 0.60,
        "overall_richness": 0.75,
    },
}

SAMPLE_REPORT_PASS = {
    "note_label": "good_note",
    "source_label": "good_source",
    "total_score": 0.95,
    "overall_passed": True,
    "rule_results": {
        "R0": {"score": 1.0, "passed": True, "issue_count": 0, "issues": []},
        "R1": {"score": 1.0, "passed": True, "issue_count": 0, "issues": []},
    },
    "summary": "All checks passed",
}


@pytest.fixture
def tmp_reports_dir(tmp_path):
    """创建临时 quality_reports 目录并写入示例报告"""
    reports_dir = tmp_path / "output" / "quality_reports"
    reports_dir.mkdir(parents=True)

    # 写入两份报告
    for name, report in [("test_note", SAMPLE_REPORT),
                         ("good_note", SAMPLE_REPORT_PASS)]:
        path = reports_dir / f"{name}_quality.json"
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False)

    return reports_dir


@pytest.fixture
def args_basic(tmp_path):
    """基础 args namespace"""
    return MagicMock(
        output_dir=str(tmp_path / "output"),
        format='table',
        verbose=False,
        quality_view=None,
        quality_list=False,
    )


# ---- _format_table tests ----

class TestFormatTable:
    def test_basic_table_output(self):
        result = _format_table(SAMPLE_REPORT)
        assert "Quality Report" in result
        assert "75%" in result
        assert "FAIL" in result
        assert "R0" in result
        assert "R3" in result

    def test_verbose_shows_issues(self):
        result = _format_table(SAMPLE_REPORT, verbose=True)
        assert "issue(s)" in result
        assert "疑似语义反转" in result
        assert "L6" in result

    def test_verbose_truncates_many_issues(self):
        """超过 10 条 issues 时截断显示"""
        many_issues_report = {
            "total_score": 0.5,
            "overall_passed": False,
            "rule_results": {
                "R1": {
                    "score": 0.0, "passed": False, "issue_count": 15,
                    "issues": [
                        {"severity": "fatal", "line_range": f"L{i}",
                         "description": f"Issue {i}",
                         "suggestion": "Fix it"}
                        for i in range(15)
                    ]
                }
            },
        }
        result = _format_table(many_issues_report, verbose=True)
        assert "and 5 more" in result

    def test_passing_report(self):
        result = _format_table(SAMPLE_REPORT_PASS)
        assert "PASS" in result
        assert "95%" in result

    def test_metrics_display(self):
        result = _format_table(SAMPLE_REPORT)
        assert "Heuristic Metrics" in result
        assert "Compression Ratio" in result
        assert "22%" in result

    def test_llm_eval_display(self):
        report = dict(SAMPLE_REPORT)
        report["llm_eval"] = {
            "richness_score": 4.0,
            "readability_score": 3.5,
            "faithfulness_score": 4.5,
            "actionability_score": 3.0,
            "overall_score": 3.8,
            "feedback": "Good overall",
            "suggestions": ["Add more examples"],
        }
        result = _format_table(report)
        assert "LLM Evaluation" in result
        assert "3.8/5" in result


# ---- _format_json tests ----

class TestFormatJson:
    def test_json_output_is_valid(self):
        result = _format_json(SAMPLE_REPORT)
        parsed = json.loads(result)
        assert parsed["total_score"] == 0.75
        assert parsed["overall_passed"] is False

    def test_json_preserves_chinese(self):
        result = _format_json(SAMPLE_REPORT)
        assert "疑似语义反转" in result


# ---- _format_md tests ----

class TestFormatMd:
    def test_md_output_contains_headers(self):
        result = _format_md(SAMPLE_REPORT)
        assert "# " in result
        assert "Quality Report" in result or "质量评估报告" in result

    def test_md_output_contains_table(self):
        result = _format_md(SAMPLE_REPORT)
        assert "| " in result  # Markdown table rows


# ---- run_quality_view tests ----

class TestRunQualityView:
    def test_no_file_specified(self, args_basic):
        args_basic.quality_view = None
        result = run_quality_view(args_basic)
        assert result == 1

    def test_nonexistent_file(self, args_basic, tmp_reports_dir):
        args_basic.quality_view = "nonexistent_note"
        result = run_quality_view(args_basic)
        assert result == 1

    def test_view_from_json_file(self, args_basic, tmp_reports_dir):
        """直接传入 JSON 报告文件"""
        report_path = tmp_reports_dir / "test_note_quality.json"
        args_basic.quality_view = str(report_path)
        args_basic.format = 'json'
        result = run_quality_view(args_basic)
        assert result == 1  # overall_passed=False

    def test_view_from_note_name(self, args_basic, tmp_reports_dir):
        """传入笔记文件名，自动查找报告"""
        note_path = tmp_reports_dir.parent / "notes" / "test_note.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# test", encoding='utf-8')

        args_basic.quality_view = str(note_path)
        args_basic.format = 'table'
        # 需要让 _find_reports_dir 找到目录
        with patch('noteforge.cli.commands.quality_view._find_reports_dir',
                   return_value=tmp_reports_dir):
            result = run_quality_view(args_basic)
            assert result == 1  # overall_passed=False

    def test_view_json_format(self, args_basic, tmp_reports_dir, capsys):
        report_path = tmp_reports_dir / "test_note_quality.json"
        args_basic.quality_view = str(report_path)
        args_basic.format = 'json'
        run_quality_view(args_basic)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["total_score"] == 0.75

    def test_view_md_format(self, args_basic, tmp_reports_dir, capsys):
        report_path = tmp_reports_dir / "test_note_quality.json"
        args_basic.quality_view = str(report_path)
        args_basic.format = 'md'
        run_quality_view(args_basic)
        captured = capsys.readouterr()
        assert "| " in captured.out  # Markdown table

    def test_view_passing_report(self, args_basic, tmp_reports_dir):
        report_path = tmp_reports_dir / "good_note_quality.json"
        args_basic.quality_view = str(report_path)
        args_basic.format = 'json'
        result = run_quality_view(args_basic)
        assert result == 0  # overall_passed=True

    def test_no_reports_dir(self, args_basic):
        args_basic.quality_view = "some_note"
        with patch('noteforge.cli.commands.quality_view._find_reports_dir',
                   return_value=None):
            result = run_quality_view(args_basic)
            assert result == 1


# ---- run_quality_list tests ----

class TestRunQualityList:
    def test_no_reports_dir(self, args_basic):
        with patch('noteforge.cli.commands.quality_view._find_reports_dir',
                   return_value=None):
            result = run_quality_list(args_basic)
            assert result == 0

    def test_empty_reports_dir(self, args_basic, tmp_path):
        reports_dir = tmp_path / "empty_reports"
        reports_dir.mkdir()
        with patch('noteforge.cli.commands.quality_view._find_reports_dir',
                   return_value=reports_dir):
            result = run_quality_list(args_basic)
            assert result == 0

    def test_list_table_format(self, args_basic, tmp_reports_dir, capsys):
        with patch('noteforge.cli.commands.quality_view._find_reports_dir',
                   return_value=tmp_reports_dir):
            result = run_quality_list(args_basic)
            assert result == 0
        captured = capsys.readouterr()
        assert "Quality Reports Summary" in captured.out
        assert "test_note" in captured.out
        assert "good_note" in captured.out

    def test_list_json_format(self, args_basic, tmp_reports_dir, capsys):
        args_basic.format = 'json'
        with patch('noteforge.cli.commands.quality_view._find_reports_dir',
                   return_value=tmp_reports_dir):
            result = run_quality_list(args_basic)
            assert result == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        # 检查摘要字段
        item = parsed[0]
        assert "file" in item
        assert "score" in item
        assert "passed" in item

    def test_list_shows_pass_fail_counts(self, args_basic, tmp_reports_dir, capsys):
        with patch('noteforge.cli.commands.quality_view._find_reports_dir',
                   return_value=tmp_reports_dir):
            run_quality_list(args_basic)
        captured = capsys.readouterr()
        assert "1 passed" in captured.out
        assert "1 failed" in captured.out

    def test_list_handles_corrupt_json(self, args_basic, tmp_reports_dir, capsys):
        """损坏的 JSON 文件不应导致崩溃"""
        bad_file = tmp_reports_dir / "corrupt_quality.json"
        bad_file.write_text("not valid json{{{", encoding='utf-8')
        with patch('noteforge.cli.commands.quality_view._find_reports_dir',
                   return_value=tmp_reports_dir):
            result = run_quality_list(args_basic)
            assert result == 0
        captured = capsys.readouterr()
        assert "ERROR" in captured.out


# ---- check --format tests ----

class TestCheckFormat:
    def test_check_json_format(self, capsys):
        """--format json 应输出 JSON"""
        engine = MagicMock()
        engine._audio_handler = MagicMock()
        engine._audio_handler.find_transcript_for_note.return_value = "test.txt"
        engine.quality_manager = MagicMock()
        engine.quality_manager.run_quality_gate.return_value = SAMPLE_REPORT
        engine.quality_manager.save_quality_report = MagicMock()
        args = MagicMock(
            check_only="test_note.md",
            format='json',
            verbose=False,
        )
        with patch('os.path.exists', return_value=True):
            result = run_check_only(engine, args)
        assert result == 1  # overall_passed=False
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["total_score"] == 0.75

    def test_check_md_format(self, capsys):
        """--format md 应输出 Markdown"""
        engine = MagicMock()
        engine._audio_handler = MagicMock()
        engine._audio_handler.find_transcript_for_note.return_value = "test.txt"
        engine.quality_manager = MagicMock()
        engine.quality_manager.run_quality_gate.return_value = SAMPLE_REPORT
        engine.quality_manager.save_quality_report = MagicMock()
        args = MagicMock(
            check_only="test_note.md",
            format='md',
            verbose=False,
        )
        with patch('os.path.exists', return_value=True):
            result = run_check_only(engine, args)
        assert result == 1
        captured = capsys.readouterr()
        assert "| " in captured.out  # Markdown table

    def test_check_table_format(self, capsys):
        """--format table (默认) 使用引擎的 check_only"""
        engine = MagicMock()
        engine.check_only.return_value = SAMPLE_REPORT
        args = MagicMock(
            check_only="test_note.md",
            format='table',
            verbose=False,
        )
        with patch('os.path.exists', return_value=True):
            result = run_check_only(engine, args)
        assert result == 1
        # table 模式下 engine.check_only 被调用
        engine.check_only.assert_called_once_with("test_note.md")

    def test_check_verbose(self, capsys):
        """--verbose 应显示详细问题列表"""
        engine = MagicMock()
        engine.check_only.return_value = SAMPLE_REPORT
        args = MagicMock(
            check_only="test_note.md",
            format='table',
            verbose=True,
        )
        with patch('os.path.exists', return_value=True):
            result = run_check_only(engine, args)
        assert result == 1
        captured = capsys.readouterr()
        assert "issue(s)" in captured.out

    def test_check_nonexistent_file(self):
        """文件不存在应返回 1"""
        engine = MagicMock()
        args = MagicMock(
            check_only="nonexistent.md",
            format='table',
            verbose=False,
        )
        with patch('os.path.exists', return_value=False):
            result = run_check_only(engine, args)
        assert result == 1

    def test_check_no_transcript(self):
        """找不到转写文件应返回 1"""
        engine = MagicMock()
        engine.check_only.return_value = None
        args = MagicMock(
            check_only="test_note.md",
            format='table',
            verbose=False,
        )
        with patch('os.path.exists', return_value=True):
            result = run_check_only(engine, args)
        assert result == 1

    def test_check_json_uses_run_quality_gate(self, capsys):
        """--format json 应直接调用 run_quality_gate 而非 check_only"""
        engine = MagicMock()
        engine._audio_handler = MagicMock()
        engine._audio_handler.find_transcript_for_note.return_value = "test.txt"
        engine.quality_manager = MagicMock()
        engine.quality_manager.run_quality_gate.return_value = SAMPLE_REPORT
        engine.quality_manager.save_quality_report = MagicMock()
        args = MagicMock(
            check_only="test_note.md",
            format='json',
            verbose=False,
        )
        with patch('os.path.exists', return_value=True):
            run_check_only(engine, args)
        # 应调用 run_quality_gate 而非 check_only
        engine.quality_manager.run_quality_gate.assert_called_once()
        engine.check_only.assert_not_called()


# ---- _report_to_quality_report tests ----

class TestReportToQualityReport:
    def test_basic_conversion(self):
        qr = _report_to_quality_report(SAMPLE_REPORT)
        assert qr.total_score == 0.75
        assert qr.overall_passed is False
        assert "R0" in qr.rule_results
        assert "R3" in qr.rule_results

    def test_issues_preserved(self):
        qr = _report_to_quality_report(SAMPLE_REPORT)
        r3 = qr.rule_results["R3"]
        assert len(r3.issues) == 2
        assert r3.issues[0].severity == "medium"
        assert "疑似语义反转" in r3.issues[0].description

    def test_llm_eval_conversion(self):
        report = dict(SAMPLE_REPORT)
        report["llm_eval"] = {
            "richness_score": 4.0,
            "readability_score": 3.5,
            "faithfulness_score": 4.5,
            "actionability_score": 3.0,
            "overall_score": 3.8,
            "feedback": "Good",
            "suggestions": ["Add more"],
        }
        qr = _report_to_quality_report(report)
        assert qr.llm_eval is not None
        assert qr.llm_eval.overall_score == 3.8

    def test_no_llm_eval(self):
        qr = _report_to_quality_report(SAMPLE_REPORT)
        assert qr.llm_eval is None

    def test_empty_report(self):
        qr = _report_to_quality_report({})
        assert qr.total_score == 0
        assert qr.overall_passed is False
        assert len(qr.rule_results) == 0
