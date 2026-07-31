# -*- coding: utf-8 -*-
"""
NoteForge CLI 批量处理增强功能单元测试

覆盖:
  - dry-run 模式（不调用 LLM，只打印计划）
  - resume 模式（加载 ExecutionTrace 断点续传）
  - min-score / max-retries 覆盖
 盖
  - progress show / clear

运行:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_cli_batch.py -v
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
import pytest

from noteforge.infra.execution_trace import ExecutionTrace


# ============================================================
# Helpers
# ============================================================

class FakeResult:
    """Minimal stand-in for GenerationResult"""

    def __init__(self, error=None, note_path='', total_score=0,
                 overall_passed=False):
        self.error = error
        self.note_path = note_path
        self.total_score = total_score
        self.overall_passed = overall_passed


def _make_engine(tmp_path):
    """Create a MagicMock engine with batch-related attributes."""
    eng = MagicMock()
    eng.base_dir = tmp_path
    eng._transcripts_dir = tmp_path / "transcripts"
    eng._notes_dir = tmp_path / "notes"
    eng._transcripts_dir.mkdir(parents=True, exist_ok=True)
    eng._notes_dir.mkdir(parents=True, exist_ok=True)
    eng.min_score = 0.80
    eng.max_retries = 2
    eng.generate_batch = MagicMock()
    eng.generate_note = MagicMock()
    eng.flush_pending_synthesis = MagicMock()
    return eng


def _make_args(**overrides):
    """Create a MagicMock args object with batch defaults."""
    defaults = dict(
        batch=True,
        title=None,
        provider=None,
        force=False,
        mode='notes',
        with_context=False,
        context_limit=3,
        skip_existing=False,
        dry_run=False,
        resume=False,
        checkpoint_file=None,
        min_score=None,
        max_retries=None,
        content_type=None,
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


# ============================================================
# Test: dry-run mode
# ============================================================

class TestDryRun:
    """dry-run 模式测试：不调用 LLM，只打印计划"""

    def test_dry_run_no_transcripts(self, tmp_path, capsys):
        """无转写文件时打印提示并返回 0"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        args = _make_args(dry_run=True)
        ret = run_batch(engine, args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '未找到转写文件' in out
        engine.generate_batch.assert_not_called()

    def test_dry_run_with_transcripts_prints_plan(self, tmp_path, capsys):
        """有转写文件时打印计划但不调用 LLM"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        # 创建转写文件
        (engine._transcripts_dir / "ep01.txt").write_text("content", encoding='utf-8')
        (engine._transcripts_dir / "ep02.txt").write_text("content", encoding='utf-8')
        args = _make_args(dry_run=True, skip_existing=True)
        ret = run_batch(engine, args)
        assert ret == 0
        out = capsys.readouterr().out
        assert 'DRY-RUN' in out
        assert 'ep01' in out
        assert 'ep02' in out
        assert '待处理' in out
        engine.generate_batch.assert_not_called()

    def test_dry_run_skips_existing_notes(self, tmp_path, capsys):
        """skip_existing 时已有笔记的文件显示在跳过列表"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        (engine._transcripts_dir / "ep01.txt").write_text("content", encoding='utf-8')
        (engine._transcripts_dir / "ep02.txt").write_text("content", encoding='utf-8')
        # ep01 已有笔记
        (engine._notes_dir / "ep01.md").write_text("# note", encoding='utf-8')
        args = _make_args(dry_run=True, skip_existing=True)
        ret = run_batch(engine, args)
        assert ret == 0
        out = capsys.readouterr().out
        assert 'ep01' in out
        assert 'ep02' in out
        assert '跳过' in out
        engine.generate_batch.assert_not_called()

    def test_dry_run_force_includes_existing(self, tmp_path, capsys):
        """force 模式下已有笔记也列入待处理"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        (engine._transcripts_dir / "ep01.txt").write_text("content", encoding='utf-8')
        (engine._notes_dir / "ep01.md").write_text("# note", encoding='utf-8')
        args = _make_args(dry_run=True, skip_existing=True, force=True)
        ret = run_batch(engine, args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '待处理' in out

    def test_dry_run_shows_quality_threshold(self, tmp_path, capsys):
        """dry-run 输出包含质量阈值信息"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        engine.min_score = 0.90
        (engine._transcripts_dir / "ep01.txt").write_text("content", encoding='utf-8')
        args = _make_args(dry_run=True)
        ret = run_batch(engine, args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '90%' in out


# ============================================================
# Test: resume mode
# ============================================================

class TestResume:
    """resume 模式测试：加载 ExecutionTrace 断点续传"""

    def test_resume_no_transcripts(self, tmp_path, capsys):
        """无转写文件时打印提示并返回 0"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        args = _make_args(resume=True)
        ret = run_batch(engine, args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '未找到转写文件' in out

    def test_resume_all_completed(self, tmp_path, capsys):
        """所有文件追踪显示已完成时跳过处理"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        (engine._transcripts_dir / "ep01.txt").write_text("content", encoding='utf-8')

        # 创建追踪文件，标记 evaluate 阶段已完成
        trace = ExecutionTrace(trace_dir=str(tmp_path / "traces"))
        trace.save("batch_ep01", [
            ExecutionTrace.StepRecord(
                stage="generate",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="abc",
                output_hash="def",
                completed_at="2026-01-01T00:00:00",
            ),
            ExecutionTrace.StepRecord(
                stage="evaluate",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="def",
                output_hash="ghi",
                completed_at="2026-01-01T00:01:00",
            ),
        ])

        args = _make_args(resume=True, checkpoint_file=str(tmp_path / "traces"))
        ret = run_batch(engine, args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '已完成' in out
        engine.generate_batch.assert_not_called()

    def test_resume_partial_calls_generate_batch(self, tmp_path, capsys):
        """部分文件未完成时只处理未完成的文件"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        (engine._transcripts_dir / "ep01.txt").write_text("content", encoding='utf-8')
        (engine._transcripts_dir / "ep02.txt").write_text("content", encoding='utf-8')

        # ep01 已完成
        trace = ExecutionTrace(trace_dir=str(tmp_path / "traces"))
        trace.save("batch_ep01", [
            ExecutionTrace.StepRecord(
                stage="evaluate",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="abc",
                output_hash="def",
                completed_at="2026-01-01T00:00:00",
            ),
        ])

        engine.generate_batch.return_value = [FakeResult(error=None)]

        args = _make_args(resume=True, checkpoint_file=str(tmp_path / "traces"))
        ret = run_batch(engine, args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '续传' in out
        engine.generate_batch.assert_called_once()
        # 只处理 ep02
        call_kwargs = engine.generate_batch.call_args[1]
        assert len(call_kwargs['transcript_paths']) == 1

    def test_resume_no_trace_processes_all(self, tmp_path, capsys):
        """无追踪记录时所有文件视为新任务"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        (engine._transcripts_dir / "ep01.txt").write_text("content", encoding='utf-8')
        (engine._transcripts_dir / "ep02.txt").write_text("content", encoding='utf-8')

        engine.generate_batch.return_value = [
            FakeResult(error=None),
            FakeResult(error=None),
        ]

        args = _make_args(resume=True, checkpoint_file=str(tmp_path / "traces"))
        ret = run_batch(engine, args)
        assert ret == 0
        engine.generate_batch.assert_called_once()
        call_kwargs = engine.generate_batch.call_args[1]
        assert len(call_kwargs['transcript_paths']) == 2

    def test_resume_with_failures_returns_1(self, tmp_path, capsys):
        """续传中有失败项时返回 1"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        (engine._transcripts_dir / "ep01.txt").write_text("content", encoding='utf-8')

        engine.generate_batch.return_value = [
            FakeResult(error='LLM failed'),
        ]

        args = _make_args(resume=True, checkpoint_file=str(tmp_path / "traces"))
        ret = run_batch(engine, args)
        assert ret == 1


# ============================================================
# Test: min-score / max-retries override
# ============================================================

class TestQualityOverride:
    """质量阈值覆盖测试"""

    def test_min_score_override(self, tmp_path, capsys):
        """--min-score 临时覆盖引擎质量阈值"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        engine.min_score = 0.80
        engine.generate_batch.return_value = [FakeResult(error=None)]
        args = _make_args(min_score=0.95)
        ret = run_batch(engine, args)
        assert engine.min_score == 0.95
        out = capsys.readouterr().out
        assert '质量阈值临时覆盖' in out
        assert '95%' in out

    def test_max_retries_override(self, tmp_path, capsys):
        """--max-retries 临时覆盖引擎最大重试次数"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        engine.max_retries = 2
        engine.generate_batch.return_value = [FakeResult(error=None)]
        args = _make_args(max_retries=5)
        ret = run_batch(engine, args)
        assert engine.max_retries == 5
        out = capsys.readouterr().out
        assert '最大重试次数临时覆盖' in out

    def test_no_override_keeps_defaults(self, tmp_path):
        """不指定覆盖参数时保持默认值"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        engine.min_score = 0.80
        engine.max_retries = 2
        engine.generate_batch.return_value = [FakeResult(error=None)]
        args = _make_args()
        run_batch(engine, args)
        assert engine.min_score == 0.80
        assert engine.max_retries == 2

    def test_min_score_none_does_not_override(self, tmp_path):
        """--min-score 未指定（None）时不覆盖"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        engine.min_score = 0.80
        engine.generate_batch.return_value = [FakeResult(error=None)]
        args = _make_args(min_score=None)
        run_batch(engine, args)
        assert engine.min_score == 0.80


# ============================================================
# Test: progress show / clear
# ============================================================

class TestProgressShow:
    """progress --show 测试"""

    def test_no_trace_dir(self, tmp_path, capsys):
        """追踪目录不存在时打印提示"""
        from noteforge.cli.commands.progress import run_progress_show
        args = MagicMock(checkpoint_file=str(tmp_path / "nonexistent"))
        ret = run_progress_show(args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '无进度数据' in out

    def test_empty_trace_dir(self, tmp_path, capsys):
        """追踪目录为空时打印提示"""
        from noteforge.cli.commands.progress import run_progress_show
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        args = MagicMock(checkpoint_file=str(trace_dir))
        ret = run_progress_show(args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '无进度数据' in out

    def test_with_completed_traces(self, tmp_path, capsys):
        """有已完成追踪时显示进度统计"""
        from noteforge.cli.commands.progress import run_progress_show
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        trace = ExecutionTrace(trace_dir=str(trace_dir))
        trace.save("batch_ep01", [
            ExecutionTrace.StepRecord(
                stage="evaluate",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="abc",
                output_hash="def",
                completed_at="2026-01-01T00:00:00",
            ),
        ])
        trace.save("batch_ep02", [
            ExecutionTrace.StepRecord(
                stage="generate",
                status=ExecutionTrace.Status.FAILED,
                input_hash="ghi",
                error_type="TRANSIENT",
            ),
        ])
        args = MagicMock(checkpoint_file=str(trace_dir))
        ret = run_progress_show(args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '已完成' in out
        assert '失败' in out
        assert '50.0%' in out

    def test_with_dead_letter_traces(self, tmp_path, capsys):
        """有死信追踪时显示死信详情"""
        from noteforge.cli.commands.progress import run_progress_show
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        trace = ExecutionTrace(trace_dir=str(trace_dir))
        trace.save("batch_ep01", [
            ExecutionTrace.StepRecord(
                stage="download",
                status=ExecutionTrace.Status.DEAD_LETTER,
                input_hash="abc",
                error_type="PERMANENT",
                completed_at="2026-01-01T00:00:00",
            ),
        ])
        args = MagicMock(checkpoint_file=str(trace_dir))
        ret = run_progress_show(args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '死信' in out


class TestProgressClear:
    """progress --clear 测试"""

    def test_no_trace_dir(self, tmp_path, capsys):
        """追踪目录不存在时打印提示"""
        from noteforge.cli.commands.progress import run_progress_clear
        args = MagicMock(checkpoint_file=str(tmp_path / "nonexistent"))
        ret = run_progress_clear(args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '无进度数据可清除' in out

    def test_empty_trace_dir(self, tmp_path, capsys):
        """追踪目录为空时打印提示"""
        from noteforge.cli.commands.progress import run_progress_clear
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        args = MagicMock(checkpoint_file=str(trace_dir))
        ret = run_progress_clear(args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '无进度数据可清除' in out

    def test_clear_deletes_trace_files(self, tmp_path, capsys):
        """清除操作删除所有追踪文件"""
        from noteforge.cli.commands.progress import run_progress_clear
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        trace = ExecutionTrace(trace_dir=str(trace_dir))
        trace.save("batch_ep01", [
            ExecutionTrace.StepRecord(
                stage="generate",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="abc",
                output_hash="def",
            ),
        ])
        trace.save("batch_ep02", [
            ExecutionTrace.StepRecord(
                stage="generate",
                status=ExecutionTrace.Status.FAILED,
                input_hash="ghi",
                error_type="TRANSIENT",
            ),
        ])
        assert len(list(trace_dir.glob('*.json'))) == 2

        args = MagicMock(checkpoint_file=str(trace_dir))
        ret = run_progress_clear(args)
        assert ret == 0
        assert len(list(trace_dir.glob('*.json'))) == 0
        out = capsys.readouterr().out
        assert '已清除 2 个追踪文件' in out


# ============================================================
# Test: standard batch mode (regression)
# ============================================================

class TestBatchStandard:
    """标准批量模式回归测试"""

    def test_no_failures_returns_0(self, tmp_path):
        """全部成功 → 返回 0"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        engine.generate_batch.return_value = [
            FakeResult(error=None),
            FakeResult(error=None),
        ]
        args = _make_args()
        ret = run_batch(engine, args)
        assert ret == 0

    def test_with_failures_returns_1(self, tmp_path):
        """有失败项 → 返回 1"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        engine.generate_batch.return_value = [
            FakeResult(error=None),
            FakeResult(error='LLM failed'),
        ]
        args = _make_args()
        ret = run_batch(engine, args)
        assert ret == 1

    def test_skipped_not_counted_as_failure(self, tmp_path):
        """"已存在（跳过）"不计为失败"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        engine.generate_batch.return_value = [
            FakeResult(error='已存在（跳过）'),
            FakeResult(error=None),
        ]
        args = _make_args()
        ret = run_batch(engine, args)
        assert ret == 0

    def test_title_warning_printed(self, tmp_path, capsys):
        """--title 在批量模式下打印警告"""
        from noteforge.cli.commands.batch_cmd import run_batch
        engine = _make_engine(tmp_path)
        engine.generate_batch.return_value = [FakeResult(error=None)]
        args = _make_args(title='should be ignored')
        ret = run_batch(engine, args)
        out = capsys.readouterr().out
        assert 'WARN' in out
