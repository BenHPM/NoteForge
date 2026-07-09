# -*- coding: utf-8 -*-
"""BatchProcessor 批量生成处理单元测试（7 tests）。"""
import os
import pytest
from unittest.mock import MagicMock

os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


class TestBatchProcessor:
    """BatchProcessor 批量生成处理测试"""

    def _make_processor(self, tmp_path):
        from noteforge.batch.processor import BatchProcessor
        from noteforge.context import PathConfig
        notes_dir = tmp_path / "notes"
        transcripts_dir = tmp_path / "transcripts"
        notes_dir.mkdir()
        transcripts_dir.mkdir()
        logger = MagicMock()
        pc = PathConfig(
            base_dir=tmp_path,
            transcripts_dir=transcripts_dir,
            notes_dir=notes_dir,
            reports_dir=tmp_path / "quality_reports",
            logs_dir=tmp_path / "logs",
        )
        return BatchProcessor(
            path_config=pc,
            logger=logger,
        )

    def test_generate_batch_skip_existing(self, tmp_path):
        """skip_existing=True 时应跳过已有笔记"""
        bp = self._make_processor(tmp_path)
        (tmp_path / "notes" / "ep01.md").write_text("已有笔记", encoding='utf-8')
        (tmp_path / "transcripts" / "ep01.txt").write_text("转写文本", encoding='utf-8')

        mock_fn = MagicMock()
        results = bp.generate_batch(
            transcript_paths=[str(tmp_path / "transcripts" / "ep01.txt")],
            generate_note_fn=mock_fn,
            skip_existing=True,
        )
        mock_fn.assert_not_called()
        assert len(results) == 1
        assert "已存在" in results[0].error

    def test_generate_batch_force_overwrite(self, tmp_path):
        """force=True 时应覆盖已有笔记"""
        bp = self._make_processor(tmp_path)
        (tmp_path / "notes" / "ep01.md").write_text("已有笔记", encoding='utf-8')
        (tmp_path / "transcripts" / "ep01.txt").write_text("转写文本", encoding='utf-8')

        from noteforge.models import GenerationResult
        mock_fn = MagicMock(return_value=GenerationResult(
            transcript_path=str(tmp_path / "transcripts" / "ep01.txt"),
            note_path=str(tmp_path / "notes" / "ep01.md"),
            total_score=0.9,
            overall_passed=True,
        ))
        results = bp.generate_batch(
            transcript_paths=[str(tmp_path / "transcripts" / "ep01.txt")],
            generate_note_fn=mock_fn,
            skip_existing=True,
            force=True,
        )
        mock_fn.assert_called_once()
        assert results[0].total_score == 0.9

    def test_generate_batch_no_transcripts(self, tmp_path):
        """无转写文件时应返回空列表"""
        bp = self._make_processor(tmp_path)
        results = bp.generate_batch(transcript_paths=[])
        assert results == []

    def test_generate_batch_calls_generate_fn(self, tmp_path):
        """批量生成应调用 generate_note_fn"""
        bp = self._make_processor(tmp_path)
        (tmp_path / "transcripts" / "ep01.txt").write_text("转写文本", encoding='utf-8')

        from noteforge.models import GenerationResult
        mock_fn = MagicMock(return_value=GenerationResult(
            transcript_path=str(tmp_path / "transcripts" / "ep01.txt"),
            total_score=0.85,
            overall_passed=True,
        ))
        results = bp.generate_batch(
            transcript_paths=[str(tmp_path / "transcripts" / "ep01.txt")],
            generate_note_fn=mock_fn,
            skip_existing=False,
        )
        mock_fn.assert_called_once()
        assert len(results) == 1

    def test_print_batch_summary_with_results(self, tmp_path, capsys):
        """print_batch_summary 应输出汇总"""
        bp = self._make_processor(tmp_path)
        from noteforge.models import GenerationResult
        results = [
            GenerationResult(
                transcript_path="/path/transcript1.txt",
                note_path="/path/note1.md",
                total_score=0.9,
                overall_passed=True,
                duration_seconds=30.0,
                token_usage={'input_tokens': 1000, 'output_tokens': 500, 'calls': 1},
            ),
            GenerationResult(
                transcript_path="/path/transcript2.txt",
                note_path="/path/note2.md",
                total_score=0.6,
                overall_passed=False,
                duration_seconds=20.0,
                token_usage={'input_tokens': 800, 'output_tokens': 400, 'calls': 1},
            ),
        ]
        bp.print_batch_summary(results)
        captured = capsys.readouterr()
        assert '批量生成汇总' in captured.out
        assert 'Token' in captured.out

    def test_print_batch_summary_with_errors(self, tmp_path, capsys):
        """print_batch_summary 应显示错误信息"""
        bp = self._make_processor(tmp_path)
        from noteforge.models import GenerationResult
        results = [
            GenerationResult(
                transcript_path="/path/transcript1.txt",
                error="LLM 调用超时",
                duration_seconds=5.0,
            ),
            GenerationResult(
                transcript_path="/path/transcript2.txt",
                error="已存在（跳过）",
                duration_seconds=0.0,
            ),
        ]
        bp.print_batch_summary(results)
        captured = capsys.readouterr()
        assert '错误' in captured.out
        assert '跳过' in captured.out

    def test_generate_batch_multiple_files(self, tmp_path):
        """批量生成应处理多个文件"""
        bp = self._make_processor(tmp_path)
        for i in range(1, 4):
            (tmp_path / "transcripts" / f"ep0{i}.txt").write_text(f"转写{i}", encoding='utf-8')

        from noteforge.models import GenerationResult
        mock_fn = MagicMock(side_effect=lambda tpath, **kwargs: GenerationResult(
            transcript_path=tpath,
            total_score=0.8,
            overall_passed=True,
        ))
        paths = [str(tmp_path / "transcripts" / f"ep0{i}.txt") for i in range(1, 4)]
        results = bp.generate_batch(
            transcript_paths=paths,
            generate_note_fn=mock_fn,
            skip_existing=False,
        )
        assert len(results) == 3
        assert mock_fn.call_count == 3
