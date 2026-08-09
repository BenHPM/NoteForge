# -*- coding: utf-8 -*-
"""
NoteForge Generate Stage 单元测试

覆盖 noteforge/engine/stages/generate.py 的 GenerateStage。

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_generate_stage.py -v
"""
import os
import pytest
import logging
from unittest.mock import MagicMock, patch

from noteforge.engine.stages.config import GenerationConfig
from noteforge.engine.stages.generate import GenerateStage
from noteforge.context import PipelineContext


@pytest.fixture
def mock_deps():
    """创建 mock 依赖"""
    prompt_builder = MagicMock()
    prompt_builder.build_system_prompt.return_value = "system prompt"
    prompt_builder.build_user_prompt.return_value = "user prompt"
    prompt_builder.build_meeting_system_prompt.return_value = "meeting system"
    prompt_builder.build_meeting_user_prompt.return_value = "meeting user"
    prompt_builder.build_feedback_prompt.return_value = "feedback prompt"

    quality_manager = MagicMock()
    quality_manager.run_quality_gate_on_text.return_value = {
        'overall_passed': True,
        'total_score': 0.9,
        'rule_results': {},
    }

    provider = MagicMock()
    provider.generate.return_value = "# 笔记内容\n\n## 核心观点\n\n- 要点一\n- 要点二"
    provider.get_usage.return_value = {'input_tokens': 1000, 'output_tokens': 500}

    return prompt_builder, quality_manager, provider


@pytest.fixture
def stage(mock_deps):
    """创建测试用 GenerateStage"""
    pb, qm, prov = mock_deps
    return GenerateStage(
        prompt_builder=pb,
        quality_manager=qm,
        provider=prov,
        config=GenerationConfig(max_retries=2, base_temperature=0.3),
    )


class TestMergeChunkNotes:
    """_merge_chunk_notes 静态方法测试"""

    def test_merge_chunk_notes_single_chunk(self):
        """单块时直接返回"""
        notes = ["# 笔记\n\n## 核心观点\n\n内容"]
        result = GenerateStage._merge_chunk_notes(notes, "标题")
        assert result == notes[0]

    def test_merge_chunk_notes_multiple_chunks(self):
        """多块时合并"""
        notes = [
            "# 笔记\n\n## 核心观点\n\n第一块内容",
            "## 核心观点\n\n第二块内容",
        ]
        result = GenerateStage._merge_chunk_notes(notes, "标题")
        assert "第一块内容" in result
        assert "第二块内容" in result

    def test_merge_chunk_notes_empty_list(self):
        """空列表时返回空字符串"""
        result = GenerateStage._merge_chunk_notes([], "标题")
        assert result == ""

    def test_merge_chunk_notes_trims_core_view_header(self):
        """多块都有"## 核心观点"时只保留一次"""
        notes = [
            "# 笔记\n\n## 核心观点\n\n第一块要点",
            "## 核心观点\n\n第二块要点",
        ]
        result = GenerateStage._merge_chunk_notes(notes, "标题")
        # 第二块从"## 核心观点"行开始截取，不会重复标题
        assert result.count("## 核心观点") >= 1


class TestGenerateStageProperties:
    """GenerateStage 属性测试"""

    def test_generate_stage_name(self, stage):
        """name 属性返回正确字符串"""
        assert stage.name == "generate"

    def test_generate_stage_init(self, mock_deps):
        """初始化参数正确设置"""
        pb, qm, prov = mock_deps
        cfg = GenerationConfig(max_retries=3, base_temperature=0.5, min_score=0.85)
        stage = GenerateStage(
            prompt_builder=pb,
            quality_manager=qm,
            provider=prov,
            config=cfg,
        )
        assert stage.config.max_retries == 3
        assert stage.config.base_temperature == 0.5
        assert stage.config.min_score == 0.85
        assert stage.prompt_builder is pb
        assert stage.quality_manager is qm
        assert stage.provider is prov


class TestGenerateStageExecute:
    """GenerateStage.execute 测试"""

    def test_generate_stage_execute_mock(self, stage, mock_deps):
        """mock LLMProvider，测试 execute() 流程"""
        pb, qm, prov = mock_deps
        ctx = PipelineContext(
            clean_text="转写文本内容",
            chunks=["转写文本内容"],
            title="测试标题",
            mode="notes",
        )
        result_ctx = stage.execute(ctx)
        assert result_ctx.note_text != ""
        assert result_ctx.error is None
        assert prov.generate.called

    def test_generate_with_quality_loop_mock(self, stage, mock_deps):
        """mock provider 和 quality_manager，测试质量反馈循环"""
        pb, qm, prov = mock_deps
        # 第一次不通过，第二次通过
        qm.run_quality_gate_on_text.side_effect = [
            {'overall_passed': False, 'total_score': 0.5, 'rule_results': {'R1': {'issues': ['问题']}}},
            {'overall_passed': True, 'total_score': 0.9, 'rule_results': {}},
        ]

        note_text, attempts = stage._generate_with_quality_loop(
            transcript="转写文本",
            chunks=["转写文本"],
            title="测试标题",
            mode="notes",
        )
        assert note_text is not None
        assert attempts >= 1


# ============================================================
# P0: 末块结构失败也重试（2026-08-09 实测末块截断直接放行）
# ============================================================

BAD_CHUNK = "# 标题\n\n## 核心观点\n\n这是一段没有结尾标点的正文内容"
GOOD_CHUNK = "# 标题\n\n## 核心观点\n\n这是一段正常的笔记内容。"


class TestChunkLastRetry:
    """P0: 末块结构验证失败也应触发重试，避免截断污染混入最终笔记"""

    def _make_stage(self, provider, cfg=None):
        pb = MagicMock()
        pb.build_user_prompt.return_value = "user"
        pb.build_system_prompt.return_value = "system"
        return GenerateStage(
            prompt_builder=pb,
            quality_manager=MagicMock(),
            provider=provider,
            config=cfg or GenerationConfig(max_retries=0, base_temperature=0.3),
        )

    def test_last_chunk_retries_on_validation_failure(self):
        """末块结构失败（非截断）也应重试"""
        provider = MagicMock()
        # 块0：首次坏 → 重试好；块1（末块）：首次坏 → 重试好
        provider.generate.side_effect = [
            BAD_CHUNK, GOOD_CHUNK,   # chunk0 首次 + 重试
            "摘要内容",              # chunk0 摘要
            BAD_CHUNK, GOOD_CHUNK,   # chunk1 首次 + 重试（P0 修复后才会发生）
        ]
        provider.get_usage.return_value = {'input_tokens': 1000, 'output_tokens': 500}
        provider._last_stop_reason = 'end_turn'  # 非截断，仅结构失败

        stage = self._make_stage(provider)
        result, attempts = stage._generate_chunked(
            ["块一" * 100, "块二" * 100], "标题", "system", 0.3,
        )
        assert result is not None
        # 末块也重试 → 共 5 次 generate（无 P0 修复时末块只生成一次，共 4 次）
        assert provider.generate.call_count == 5

    def test_last_chunk_retries_on_truncation(self):
        """末块截断（stop_reason=max_tokens）也应重试"""
        provider = MagicMock()
        provider.generate.side_effect = [
            BAD_CHUNK, GOOD_CHUNK,
            "摘要内容",
            BAD_CHUNK, GOOD_CHUNK,
        ]
        provider.get_usage.return_value = {'input_tokens': 1000, 'output_tokens': 500}
        provider._last_stop_reason = 'max_tokens'

        stage = self._make_stage(provider)
        result, attempts = stage._generate_chunked(
            ["块一" * 100, "块二" * 100], "标题", "system", 0.3,
        )
        assert result is not None
        assert provider.generate.call_count == 5

    def test_truncation_retry_raises_max_tokens(self):
        """截断重试应提高 max_tokens 避免二次截断"""
        provider = MagicMock()
        provider.generate.side_effect = [
            BAD_CHUNK, GOOD_CHUNK,
            "摘要内容",
            BAD_CHUNK, GOOD_CHUNK,
        ]
        provider.get_usage.return_value = {'input_tokens': 1000, 'output_tokens': 500}
        provider._last_stop_reason = 'max_tokens'

        cfg = GenerationConfig(
            max_retries=0, base_temperature=0.3,
            max_tokens=8192, truncation_retry_max_tokens=16384,
        )
        stage = self._make_stage(provider, cfg)
        stage._generate_chunked(
            ["块一" * 100, "块二" * 100], "标题", "system", 0.3,
        )

        # 2 次截断重试（chunk0+chunk1）应使用 16384；普通调用用 8192；摘要用 1024
        bump_calls = [
            c for c in provider.generate.call_args_list
            if c.kwargs.get('max_tokens', 0) > 8192
        ]
        assert len(bump_calls) == 2
        for c in bump_calls:
            assert c.kwargs['max_tokens'] == 16384

    def test_no_truncation_no_max_tokens_bump(self):
        """非截断的结构失败重试不应提高 max_tokens"""
        provider = MagicMock()
        provider.generate.side_effect = [
            BAD_CHUNK, GOOD_CHUNK,
            "摘要内容",
            BAD_CHUNK, GOOD_CHUNK,
        ]
        provider.get_usage.return_value = {'input_tokens': 1000, 'output_tokens': 500}
        provider._last_stop_reason = 'end_turn'

        cfg = GenerationConfig(
            max_retries=0, base_temperature=0.3,
            max_tokens=8192, truncation_retry_max_tokens=16384,
        )
        stage = self._make_stage(provider, cfg)
        stage._generate_chunked(
            ["块一" * 100, "块二" * 100], "标题", "system", 0.3,
        )

        # 所有调用 max_tokens 都 <= 8192（8192 或 1024）
        for c in provider.generate.call_args_list:
            assert c.kwargs.get('max_tokens', 8192) <= 8192
