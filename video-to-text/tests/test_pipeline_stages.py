# -*- coding: utf-8 -*-
"""
NoteForge Pipeline + Stages 单元测试

覆盖 Pipeline 编排器和各 Stage 的核心逻辑。
所有外部依赖通过 mock 替代。
"""
import os
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

# ============================================================
# PipelineContext
# ============================================================

class TestPipelineContext:
    """PipelineContext 数据类测试"""

    def test_default_values(self):
        from noteforge.context import PipelineContext
        ctx = PipelineContext()
        assert ctx.content_type == "lecture"
        assert ctx.mode == "notes"
        assert ctx.error is None
        assert ctx.chunks == []
        assert ctx.token_usage == {}

    def test_is_audio_source(self):
        from noteforge.context import PipelineContext
        assert PipelineContext(source_path="audio.mp3").is_audio_source
        assert PipelineContext(source_path="video.mp4").is_audio_source
        assert not PipelineContext(source_path="text.txt").is_audio_source
        assert not PipelineContext(source_path="ep01").is_audio_source

    def test_source_stem(self):
        from noteforge.context import PipelineContext
        assert PipelineContext(source_path="/path/ep01.txt").source_stem == "ep01"


# ============================================================
# Pipeline
# ============================================================

class TestPipeline:
    """Pipeline 编排器测试"""

    def _make_stage(self, name, side_effect=None):
        """创建 mock stage — execute 必须返回 ctx"""
        stage = MagicMock()
        stage.name = name
        if side_effect:
            stage.execute.side_effect = side_effect
        else:
            # 默认：execute(ctx) 返回 ctx 自身
            stage.execute = MagicMock(side_effect=lambda ctx: ctx)
        return stage

    def test_add_and_run(self):
        """add_stage + run 正常流程"""
        from noteforge.engine.pipeline import Pipeline
        from noteforge.context import PipelineContext

        s1 = self._make_stage("stage1")
        s2 = self._make_stage("stage2")
        p = Pipeline()
        p.add_stage(s1)
        p.add_stage(s2)
        ctx = PipelineContext(source_path="test.txt")
        result = p.run(ctx)
        assert result.error is None
        s1.execute.assert_called_once()
        s2.execute.assert_called_once()

    def test_stage_names(self):
        """stage_names 属性"""
        from noteforge.engine.pipeline import Pipeline
        p = Pipeline([self._make_stage("a"), self._make_stage("b")])
        assert p.stage_names == ["a", "b"]

    def test_exception_breaks_pipeline(self):
        """stage 抛异常 → 中断后续 stage"""
        from noteforge.engine.pipeline import Pipeline
        from noteforge.context import PipelineContext

        s1 = self._make_stage("fail", side_effect=RuntimeError("boom"))
        s2 = self._make_stage("skip")
        p = Pipeline([s1, s2])
        ctx = PipelineContext()
        result = p.run(ctx)
        assert result.error is not None
        assert "fail" in result.error
        assert "boom" in result.error
        s2.execute.assert_not_called()

    def test_ctx_error_breaks_pipeline(self):
        """stage 设置 ctx.error → 中断后续 stage"""
        from noteforge.engine.pipeline import Pipeline
        from noteforge.context import PipelineContext

        def _set_error(ctx):
            ctx.error = "something failed"
            return ctx

        s1 = self._make_stage("fail")
        s1.execute = _set_error
        s2 = self._make_stage("skip")
        p = Pipeline([s1, s2])
        ctx = PipelineContext()
        result = p.run(ctx)
        assert result.error == "something failed"

    def test_empty_pipeline(self):
        """空 pipeline → 不报错"""
        from noteforge.engine.pipeline import Pipeline
        from noteforge.context import PipelineContext
        p = Pipeline()
        ctx = PipelineContext()
        result = p.run(ctx)
        assert result.error is None


# ============================================================
# PreprocessStage
# ============================================================

class TestPreprocessStage:
    """PreprocessStage 测试"""

    def test_short_text_sets_error(self):
        """短文本 <100 字符 → 设置 ctx.error"""
        from noteforge.engine.stages.preprocess import PreprocessStage
        from noteforge.context import PipelineContext

        preprocessor = MagicMock()
        preprocessor.clean.return_value = "短"
        preprocessor.get_transcript_stats.return_value = {
            'char_count': 1, 'word_count': 1, 'estimated_tokens': 1
        }
        preprocessor.chunk_if_needed.return_value = ["短"]

        stage = PreprocessStage(preprocessor, {})
        ctx = PipelineContext(
            transcript_path="test.txt",
            raw_text="短",
            clean_text="短",
        )
        with patch('noteforge.engine.stages.preprocess.read_file', return_value="短"):
            result = stage.execute(ctx)
        assert result.error is not None

    def test_name(self):
        from noteforge.engine.stages.preprocess import PreprocessStage
        stage = PreprocessStage(MagicMock(), {})
        assert stage.name == "preprocess"


# ============================================================
# FormatStage
# ============================================================

class TestFormatStage:
    """FormatStage 测试"""

    def test_normal_format(self):
        """正常格式化流程"""
        from noteforge.engine.stages.format import FormatStage
        from noteforge.context import PipelineContext

        formatter = MagicMock()
        formatter.format.return_value = "# 格式化笔记\n\n内容"
        formatter.validate_structure.return_value = []

        stage = FormatStage(formatter, content_type='lecture')
        ctx = PipelineContext(note_text="原始笔记", title="测试", transcript_path="t.txt", clean_text="清洗")
        result = stage.execute(ctx)
        assert result.formatted_text == "# 格式化笔记\n\n内容"
        assert result.structural_issues == []

    def test_structural_issues(self):
        """结构校验发现问题"""
        from noteforge.engine.stages.format import FormatStage
        from noteforge.context import PipelineContext

        formatter = MagicMock()
        formatter.format.return_value = "内容"
        formatter.validate_structure.return_value = ["缺少二级标题"]

        stage = FormatStage(formatter)
        ctx = PipelineContext(note_text="笔记", title="测试", transcript_path="t.txt")
        result = stage.execute(ctx)
        assert result.structural_issues == ["缺少二级标题"]

    def test_name(self):
        from noteforge.engine.stages.format import FormatStage
        stage = FormatStage(MagicMock())
        assert stage.name == "format"


# ============================================================
# SaveStage
# ============================================================

class TestSaveStage:
    """SaveStage 测试"""

    def test_normal_save(self):
        """正常保存（通过质量门禁）"""
        from noteforge.engine.stages.save import SaveStage
        from noteforge.context import PipelineContext

        stage = SaveStage(notes_dir=Path("/tmp/notes"))
        ctx = PipelineContext(
            output_path="/tmp/notes/test.md",
            formatted_text="# 测试笔记\n\n内容",
            title="测试",
            overall_passed=True,
            quality_report={'total_score': 0.9, 'overall_passed': True},
        )
        with patch('noteforge.engine.stages.save.write_file') as mock_write, \
             patch('shutil.copy2'), \
             patch('os.path.exists', return_value=False):
            result = stage.execute(ctx)
            mock_write.assert_called_once()

    def test_failed_quality_gate_skips_save(self):
        """未通过质量门禁时不保存（避免孤立文件）"""
        from noteforge.engine.stages.save import SaveStage
        from noteforge.context import PipelineContext

        stage = SaveStage(notes_dir=Path("/tmp/notes"))
        ctx = PipelineContext(
            output_path="/tmp/notes/test.md",
            formatted_text="# 测试笔记\n\n内容",
            overall_passed=False,
            quality_report={'total_score': 0.3, 'overall_passed': False},
        )
        with patch('noteforge.engine.stages.save.write_file') as mock_write:
            result = stage.execute(ctx)
            mock_write.assert_not_called()

    def test_no_quality_report_still_saves(self):
        """质量门禁未运行时仍保存（兼容模式）"""
        from noteforge.engine.stages.save import SaveStage
        from noteforge.context import PipelineContext

        stage = SaveStage(notes_dir=Path("/tmp/notes"))
        ctx = PipelineContext(
            output_path="/tmp/notes/test.md",
            formatted_text="# 测试笔记\n\n内容",
            quality_report=None,
        )
        with patch('noteforge.engine.stages.save.write_file') as mock_write:
            result = stage.execute(ctx)
            mock_write.assert_called_once()

    def test_name(self):
        from noteforge.engine.stages.save import SaveStage
        stage = SaveStage(notes_dir=Path("/tmp"))
        assert stage.name == "save"


# ============================================================
# QualityGateStage
# ============================================================

class TestQualityGateStage:
    """QualityGateStage 测试"""

    def test_normal_evaluate(self):
        """正常评估流程"""
        from noteforge.engine.stages.evaluate import QualityGateStage
        from noteforge.context import PipelineContext

        qm = MagicMock()
        qm.run_quality_gate_on_text.return_value = {
            'total_score': 0.85, 'overall_passed': True, 'rule_results': {}
        }

        stage = QualityGateStage(qm, reports_dir=Path("/tmp/reports"))
        ctx = PipelineContext(
            formatted_text="# 笔记\n\n内容",
            clean_text="转写原文",
        )
        result = stage.execute(ctx)
        assert result.total_score == 0.85
        assert result.overall_passed is True

    def test_no_report_skips_save(self):
        """无报告时不崩溃"""
        from noteforge.engine.stages.evaluate import QualityGateStage
        from noteforge.context import PipelineContext

        qm = MagicMock()
        qm.run_quality_gate_on_text.return_value = None

        stage = QualityGateStage(qm, reports_dir=Path("/tmp/reports"))
        ctx = PipelineContext(
            formatted_text="# 笔记\n\n内容",
            clean_text="转写原文",
        )
        result = stage.execute(ctx)
        # Should not crash, report is None
        assert result.quality_report is None

    def test_name(self):
        from noteforge.engine.stages.evaluate import QualityGateStage
        stage = QualityGateStage(MagicMock(), reports_dir=Path("/tmp"))
        assert stage.name == "quality_gate"


# ============================================================
# PostProcessStage
# ============================================================

class TestPostProcessStage:
    """PostProcessStage 测试"""

    def test_token_usage_recorded(self):
        """Token 使用统计被记录"""
        from noteforge.engine.stages.postprocess import PostProcessStage
        from noteforge.context import PipelineContext

        usage_fn = MagicMock(return_value={
            'input_tokens': 1000, 'output_tokens': 500, 'calls': 1
        })
        stage = PostProcessStage(get_total_usage_fn=usage_fn)
        ctx = PipelineContext(output_path="/tmp/notes/test.md", formatted_text="内容")
        result = stage.execute(ctx)
        assert result.token_usage['input_tokens'] == 1000

    def test_feishu_sync_exception_does_not_break(self):
        """飞书同步异常不阻断主流程"""
        from noteforge.engine.stages.postprocess import PostProcessStage
        from noteforge.context import PipelineContext

        sync_fn = MagicMock(side_effect=RuntimeError("飞书挂了"))
        stage = PostProcessStage(try_feishu_sync_fn=sync_fn)
        ctx = PipelineContext(output_path="/tmp/notes/test.md", formatted_text="内容")
        result = stage.execute(ctx)
        assert result.error is None  # 不阻断

    def test_synthesis_exception_does_not_break(self):
        """合成触发异常不阻断主流程"""
        from noteforge.engine.stages.postprocess import PostProcessStage
        from noteforge.context import PipelineContext

        synth_fn = MagicMock(side_effect=RuntimeError("合成失败"))
        stage = PostProcessStage(auto_trigger_synthesis_fn=synth_fn)
        ctx = PipelineContext(output_path="/tmp/notes/test.md", formatted_text="内容")
        result = stage.execute(ctx)
        assert result.error is None  # 不阻断

    def test_name(self):
        from noteforge.engine.stages.postprocess import PostProcessStage
        stage = PostProcessStage()
        assert stage.name == "postprocess"


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
