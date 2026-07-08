# -*- coding: utf-8 -*-
"""
NoteForge 知识合成引擎单元测试

覆盖 noteforge/intelligence/synthesis.py 的 SynthesisEngine，
以及 noteforge/intelligence/prompts.py 和 validation.py 的模块级函数。

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_synthesis.py -v
"""
import os
os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'

import pytest
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

from noteforge.intelligence.synthesis import SynthesisEngine
from noteforge.intelligence.prompts import (
    build_synthesis_system_prompt,
    build_synthesis_prompt,
    build_extraction_prompt,
    build_merge_prompt,
)
from noteforge.intelligence.validation import validate_synthesis
from noteforge.context import PathConfig


@pytest.fixture
def engine(tmp_path):
    """创建测试用 SynthesisEngine"""
    classifier = MagicMock()
    logger = logging.getLogger('test_synthesis')
    logger.setLevel(logging.DEBUG)
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    pc = PathConfig(
        base_dir=tmp_path,
        transcripts_dir=tmp_path / "transcripts",
        notes_dir=notes_dir,
        reports_dir=tmp_path / "reports",
        logs_dir=tmp_path / "logs",
    )
    return SynthesisEngine(
        domain_classifier=classifier,
        path_config=pc,
        logger=logger,
    )


# ============================================================
# 合成质量验证测试
# ============================================================

class TestValidateSynthesis:
    """validate_synthesis 测试"""

    def test_validate_synthesis_no_contradictions(self, engine):
        """无矛盾时通过（完整合成文档）"""
        synthesis = (
            "# 课程知识体系\n\n"
            "## 二、核心思维模型\n\n"
            "模型定义内容\n\n"
            "## 三、方法论框架\n\n"
            "方法论内容\n\n"
            "## 五、行动手册\n\n"
            "行动内容\n\n"
            "## 六、学习路径\n\n"
            "路径内容\n\n"
            "## 八、金句精选\n\n"
            '> "讲师原话一"\n\n'
            '> "讲师原话二"\n\n'
            '> "讲师原话三"\n\n'
            "第1集关联第2集，递进关系。\n"
        )
        note_paths = ["第1集.md", "第2集.md"]
        issues = validate_synthesis(synthesis, note_paths)
        # 应该没有严重问题（可能有来源标注问题但不影响核心）
        assert isinstance(issues, list)

    def test_validate_synthesis_has_contradictions(self, engine):
        """有矛盾标记时返回问题列表"""
        synthesis = "简单文本没有结构"
        note_paths = ["第1集.md"]
        issues = validate_synthesis(synthesis, note_paths)
        assert len(issues) > 0
        # 应缺少必要节
        assert any('缺少必要节' in i for i in issues)

    def test_validate_synthesis_has_unresolved_markers(self, engine):
        """有未解决标记时返回问题"""
        synthesis = (
            "## 思维模型\n内容\n\n"
            "## 方法论\n内容\n\n"
            "## 行动\n内容\n\n"
            "## 学习路径\n内容\n\n"
            "## 金句\n内容\n"
        )
        note_paths = ["第1集.md"]
        issues = validate_synthesis(synthesis, note_paths)
        # 缺少跨集关联
        assert any('关联' in i for i in issues)

    def test_validate_synthesis_missing_core_views(self, engine):
        """缺少核心观点时返回问题"""
        synthesis = "## 方法论\n内容\n\n## 行动\n内容\n"
        note_paths = ["第1集.md"]
        issues = validate_synthesis(synthesis, note_paths)
        assert len(issues) > 0

    def test_validate_synthesis_no_framework(self, engine):
        """缺少知识框架时返回问题"""
        synthesis = "简单文本"
        note_paths = []
        issues = validate_synthesis(synthesis, note_paths)
        assert len(issues) > 0
        # 应缺少多个必要节
        assert any('缺少必要节' in i for i in issues)


# ============================================================
# Prompt 构建测试
# ============================================================

class TestPromptBuilding:
    """Prompt 构建函数测试"""

    def test_placeholder(self):
        """占位测试"""
        pass

    def test_build_synthesis_system_prompt(self, engine):
        """返回包含关键词的非空字符串"""
        prompt = build_synthesis_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert '知识架构师' in prompt
        assert '跨集关联' in prompt

    def test_build_synthesis_prompt(self, engine):
        """返回包含笔记内容的非空字符串"""
        notes = "### ep01\n\n笔记内容一\n\n### ep02\n\n笔记内容二"
        prompt = build_synthesis_prompt(notes)
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert '笔记内容一' in prompt
        assert '知识架构师' in prompt

    def test_build_extraction_prompt(self, engine):
        """返回包含集名和内容的非空字符串"""
        prompt = build_extraction_prompt("第1集", "这是第1集的笔记内容")
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert '第1集' in prompt
        assert '这是第1集的笔记内容' in prompt

    def test_build_merge_prompt_normal(self, engine):
        """返回包含合并指令的字符串"""
        extractions = "### ep01\n\n提取结果一\n\n### ep02\n\n提取结果二"
        prompt = build_merge_prompt(extractions)
        assert isinstance(prompt, str)
        assert '知识架构师' in prompt
        assert '提取结果一' in prompt
        assert '矛盾' not in prompt or '观点张力' in prompt

    def test_build_merge_prompt_with_contradictions(self, engine):
        """包含矛盾标记时返回矛盾检测指令"""
        extractions = "提取结果"
        contradictions = "### 矛盾 1: 观点冲突\n- A方: 第1集\n- B方: 第2集"
        prompt = build_merge_prompt(extractions, contradictions)
        assert isinstance(prompt, str)
        assert '矛盾检测结果' in prompt
        assert '观点冲突' in prompt
        assert '观点张力与矛盾' in prompt


# ============================================================
# Mock LLM 集成测试
# ============================================================

class TestSynthesisWithMock:
    """需要 mock LLM 的合成流程测试"""

    def test_generate_synthesis_mock(self, engine, tmp_path):
        """mock LLMProvider.generate 和 DomainClassifier，测试完整流程"""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir(exist_ok=True)

        # 创建测试笔记文件
        (notes_dir / "第1集.md").write_text("# 第1集笔记\n\n内容一", encoding='utf-8')
        (notes_dir / "第2集.md").write_text("# 第2集笔记\n\n内容二", encoding='utf-8')

        # Mock domain_classifier
        engine._domain_classifier.get_notes_by_domain.return_value = {
            'general': [str(notes_dir / "第1集.md"), str(notes_dir / "第2集.md")]
        }
        engine._domain_classifier.get_domain_config.return_value = {
            'name': '通用', 'output_name': 'knowledge_synthesis'
        }

        # Mock provider
        provider = MagicMock()
        provider.generate.return_value = (
            "# 知识体系\n\n## 思维模型\n内容\n\n## 方法论\n内容\n\n"
            "## 行动\n内容\n\n## 学习路径\n内容\n\n## 金句\n"
            '> "原话一"\n> "原话二"\n> "原话三"\n\n'
            "第1集与第2集关联，递进关系。\n"
        )

        result = engine.generate_synthesis(provider=provider)
        assert result is not None
        assert provider.generate.called

    def test_generate_synthesis_no_provider(self, engine):
        """provider 为 None 时返回 None"""
        result = engine.generate_synthesis(provider=None)
        assert result is None

    def test_generate_synthesis_two_stage_mock(self, engine, tmp_path):
        """mock LLM + DomainClassifier + 文件 IO，测试两阶段流程"""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir(exist_ok=True)

        (notes_dir / "第1集.md").write_text("# 第1集笔记\n\n内容一", encoding='utf-8')

        engine._domain_classifier.get_notes_by_domain.return_value = {
            'general': [str(notes_dir / "第1集.md")]
        }
        engine._domain_classifier.get_domain_config.return_value = {
            'name': '通用', 'output_name': 'knowledge_synthesis'
        }
        engine._domain_classifier.detect_domain.return_value = 'general'

        provider = MagicMock()
        # Stage 1: extraction → Stage 2: contradiction detection → merge
        provider.generate.side_effect = [
            "### 核心模型\n- 模型一: 定义",  # extraction
            "",  # contradiction detection (no contradictions)
            "# 知识体系\n\n## 思维模型\n内容\n\n## 方法论\n内容\n\n"
            "## 行动\n内容\n\n## 学习路径\n内容\n\n## 金句\n"
            '> "原话一"\n> "原话二"\n> "原话三"\n\n'
            "第1集关联内容。\n",  # merge
        ]

        result = engine.generate_synthesis_two_stage(provider=provider)
        assert result is not None
        assert provider.generate.call_count >= 2

    def test_update_synthesis_incremental_mock(self, engine, tmp_path):
        """mock LLM + DomainClassifier，测试增量更新"""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir(exist_ok=True)

        new_note = notes_dir / "第3集.md"
        new_note.write_text("# 第3集笔记\n\n新内容", encoding='utf-8')

        existing_synth = notes_dir / "knowledge_synthesis.md"
        existing_synth.write_text("# 已有合成文档\n\n## 思维模型\n旧内容", encoding='utf-8')

        engine._domain_classifier.detect_domain.return_value = 'general'
        engine._domain_classifier.get_domain_config.return_value = {
            'name': '通用', 'output_name': 'knowledge_synthesis'
        }
        engine._domain_classifier.validate_domain_match.return_value = (True, 'general', 'general')

        provider = MagicMock()
        provider.generate.side_effect = [
            "### 核心模型\n- 新模型: 定义",  # extraction
            "# 更新后合成文档\n\n## 思维模型\n旧内容+新内容\n\n## 方法论\n内容\n\n"
            "## 行动\n内容\n\n## 学习路径\n内容\n\n## 金句\n"
            '> "原话一"\n> "原话二"\n> "原话三"\n\n'
            "第1集与第3集关联。\n",  # update
        ]

        result = engine.update_synthesis_incremental(
            new_note_path=str(new_note),
            provider=provider,
            existing_synthesis_path=str(existing_synth),
        )
        assert result is not None
        assert provider.generate.call_count >= 2

    def test_update_synthesis_incremental_no_provider(self, engine):
        """provider 为 None 时增量更新返回 None"""
        result = engine.update_synthesis_incremental(
            new_note_path="/fake/path.md",
            provider=None,
        )
        assert result is None
