# -*- coding: utf-8 -*-
"""
NoteForge PathConfig / PipelineContext 单元测试

覆盖 noteforge/context.py 的 PathConfig 和 PipelineContext。

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_path_config.py -v
"""
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# 跳过 env_check
os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


# ============================================================
# PathConfig 测试
# ============================================================

class TestPathConfig:
    """PathConfig 共享路径配置测试"""

    def test_path_config_defaults(self):
        """默认值应正确设置"""
        from noteforge.context import PathConfig
        pc = PathConfig(
            base_dir=Path("/base"),
            transcripts_dir=Path("/base/output/transcripts"),
            notes_dir=Path("/base/output/notes"),
            reports_dir=Path("/base/output/quality_reports"),
            logs_dir=Path("/base/output/logs"),
        )
        assert pc.base_dir == Path("/base")
        assert pc.transcripts_dir == Path("/base/output/transcripts")
        assert pc.notes_dir == Path("/base/output/notes")
        assert pc.reports_dir == Path("/base/output/quality_reports")
        assert pc.logs_dir == Path("/base/output/logs")

    def test_path_config_mutation(self):
        """修改 PathConfig 字段后应反映新值"""
        from noteforge.context import PathConfig
        pc = PathConfig(
            base_dir=Path("/base"),
            transcripts_dir=Path("/base/output/transcripts"),
            notes_dir=Path("/base/output/notes"),
            reports_dir=Path("/base/output/quality_reports"),
            logs_dir=Path("/base/output/logs"),
        )
        pc.notes_dir = Path("/custom/notes")
        assert pc.notes_dir == Path("/custom/notes")

    def test_path_config_mutation_visible_to_holders(self):
        """修改 PathConfig 后，持有引用的子组件能看到新值"""
        from noteforge.context import PathConfig
        pc = PathConfig(
            base_dir=Path("/base"),
            transcripts_dir=Path("/base/output/transcripts"),
            notes_dir=Path("/base/output/notes"),
            reports_dir=Path("/base/output/quality_reports"),
            logs_dir=Path("/base/output/logs"),
        )
        # 模拟子组件持有 PathConfig 引用
        class SubComponent:
            def __init__(self, path_config):
                self.path_config = path_config
        comp = SubComponent(pc)
        pc.notes_dir = Path("/new/notes")
        assert comp.path_config.notes_dir == Path("/new/notes")

    def test_path_config_shared_reference(self):
        """多个组件共享同一 PathConfig 实例"""
        from noteforge.context import PathConfig
        pc = PathConfig(
            base_dir=Path("/base"),
            transcripts_dir=Path("/base/output/transcripts"),
            notes_dir=Path("/base/output/notes"),
            reports_dir=Path("/base/output/quality_reports"),
            logs_dir=Path("/base/output/logs"),
        )
        comp_a = type("A", (), {"pc": pc})()
        comp_b = type("B", (), {"pc": pc})()
        pc.logs_dir = Path("/shared/logs")
        assert comp_a.pc.logs_dir == Path("/shared/logs")
        assert comp_b.pc.logs_dir == Path("/shared/logs")

    def test_path_config_all_path_type(self):
        """PathConfig 所有字段应为 Path 类型"""
        from noteforge.context import PathConfig
        pc = PathConfig(
            base_dir=Path("/base"),
            transcripts_dir=Path("/base/t"),
            notes_dir=Path("/base/n"),
            reports_dir=Path("/base/r"),
            logs_dir=Path("/base/l"),
        )
        for field_name in ['base_dir', 'transcripts_dir', 'notes_dir', 'reports_dir', 'logs_dir']:
            assert isinstance(getattr(pc, field_name), Path), f"{field_name} 应为 Path 类型"


# ============================================================
# PipelineContext 测试
# ============================================================

class TestPipelineContext:
    """PipelineContext 流水线上下文测试"""

    def test_pipeline_context_defaults(self):
        """PipelineContext 默认值应正确"""
        from noteforge.context import PipelineContext
        ctx = PipelineContext()
        assert ctx.source_path == ""
        assert ctx.output_path == ""
        assert ctx.title == ""
        assert ctx.content_type == "lecture"
        assert ctx.mode == "notes"
        assert ctx.force is False
        assert ctx.with_context is False
        assert ctx.context_limit == 3
        assert ctx.attempts == 0
        assert ctx.overall_passed is False

    def test_pipeline_context_is_audio_source(self):
        """is_audio_source 应正确判断音频/视频扩展名"""
        from noteforge.context import PipelineContext
        audio_exts = ['.mp3', '.wav', '.m4a', '.flac', '.mp4', '.mkv', '.avi', '.mov']
        for ext in audio_exts:
            ctx = PipelineContext(source_path=f"/path/to/file{ext}")
            assert ctx.is_audio_source is True, f"{ext} 应被识别为音频源"

        ctx = PipelineContext(source_path="/path/to/file.txt")
        assert ctx.is_audio_source is False

    def test_pipeline_context_source_stem(self):
        """source_stem 应返回无扩展名的文件名"""
        from noteforge.context import PipelineContext
        ctx = PipelineContext(source_path="/path/to/ep01.mp3")
        assert ctx.source_stem == "ep01"

    def test_pipeline_context_source_stem_no_ext(self):
        """source_stem 无扩展名时返回完整文件名"""
        from noteforge.context import PipelineContext
        ctx = PipelineContext(source_path="/path/to/ep01")
        assert ctx.source_stem == "ep01"

    def test_pipeline_context_mutable_fields(self):
        """可变字段（chunks, warnings 等）应独立初始化"""
        from noteforge.context import PipelineContext
        ctx1 = PipelineContext()
        ctx2 = PipelineContext()
        ctx1.chunks.append("chunk1")
        ctx1.warnings.append("warn1")
        assert ctx2.chunks == []
        assert ctx2.warnings == []

    def test_pipeline_context_with_path_config(self):
        """PipelineContext 与 PathConfig 协作"""
        from noteforge.context import PipelineContext, PathConfig
        pc = PathConfig(
            base_dir=Path("/base"),
            transcripts_dir=Path("/base/t"),
            notes_dir=Path("/base/n"),
            reports_dir=Path("/base/r"),
            logs_dir=Path("/base/l"),
        )
        ctx = PipelineContext(
            source_path=str(pc.transcripts_dir / "ep01.txt"),
            output_path=str(pc.notes_dir / "ep01.md"),
        )
        assert "ep01.txt" in ctx.source_path
        assert "ep01.md" in ctx.output_path
        assert ctx.source_stem == "ep01"

    def test_pipeline_context_quality_report_default_none(self):
        """quality_report 默认为 None"""
        from noteforge.context import PipelineContext
        ctx = PipelineContext()
        assert ctx.quality_report is None
        assert ctx.error is None

    def test_pipeline_context_token_usage_independent(self):
        """token_usage 应独立初始化"""
        from noteforge.context import PipelineContext
        ctx1 = PipelineContext()
        ctx2 = PipelineContext()
        ctx1.token_usage["input"] = 100
        assert "input" not in ctx2.token_usage
