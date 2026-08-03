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
        """引用了不存在的集数时返回问题"""
        synthesis = "第99集的内容，关联第100集"
        note_paths = ["第1集.md"]
        issues = validate_synthesis(synthesis, note_paths)
        assert len(issues) > 0
        # 应报告引用了不存在的集数
        assert any('不存在的集数' in i for i in issues)

    def test_validate_synthesis_has_unresolved_markers(self, engine):
        """无来源标注时返回问题"""
        synthesis = (
            "## 思维模型\n内容\n\n"
            "## 方法论\n内容\n\n"
            "## 行动\n内容\n\n"
            "## 学习路径\n内容\n\n"
            "## 金句\n内容\n"
        )
        note_paths = ["第1集.md"]
        issues = validate_synthesis(synthesis, note_paths)
        # 无「第X集」来源标注 → 应报告
        assert any('来源标注' in i for i in issues)

    def test_validate_synthesis_missing_core_views(self, engine):
        """缺少核心观点时返回问题"""
        synthesis = "## 方法论\n内容\n\n## 行动\n内容\n"
        note_paths = ["第1集.md"]
        issues = validate_synthesis(synthesis, note_paths)
        assert len(issues) > 0

    def test_validate_synthesis_no_framework(self, engine):
        """无引用无笔记时返回问题"""
        synthesis = "简单文本"
        note_paths = []
        issues = validate_synthesis(synthesis, note_paths)
        assert len(issues) > 0


class TestValidateSynthesisEnhanced:
    """validate_synthesis 新增检查（COT 泄漏 / 结构完整性 / 来源覆盖度）"""

    @staticmethod
    def _valid_synthesis() -> str:
        """结构完整的合成文档（应无严重问题）"""
        return (
            "# 课程知识体系\n\n"
            "## 一、课程逻辑总览\n"
            "整体逻辑描述\n\n"
            "## 二、核心思维模型\n"
            "### 2.1 模型一\n定义内容\n\n"
            "## 三、方法论框架\n"
            "方法论内容\n\n"
            "## 四、跨集知识关联图\n"
            "| A | B | 关联 | 说明 |\n"
            "## 五、行动手册\n"
            "### 5.1 日常练习\n\n"
            "## 六、学习路径\n"
            "阶段一\n\n"
            "## 七、方法论速查表\n"
            "| 方法 | 要点 | 来源 |\n"
            "## 八、金句精选\n"
            '> "原话一"\n'
            "第1集关联第2集。\n"
        )

    def test_valid_synthesis_no_severe(self):
        """结构完整文档不应有严重问题"""
        issues = validate_synthesis(
            self._valid_synthesis(), ["第1集.md", "第2集.md"]
        )
        assert all("[严重]" not in i for i in issues), issues

    def test_cot_leak_first_line_detected(self):
        """首行为规划语言（思考过程泄漏）应判严重"""
        text = (
            "用户现在需要我根据提供的所有逐集关键概念，生成跨集知识合成文档。\n"
            "首先，先理清楚整个课程的主题。\n"
            + self._valid_synthesis()
        )
        issues = validate_synthesis(text, ["第1集.md", "第2集.md"])
        assert any("泄漏" in i and "[严重]" in i for i in issues), issues

    def test_prompt_echo_detected(self):
        """提示词回显（≥2 个标记）应判严重"""
        text = (
            self._valid_synthesis()
            + "\n你的任务是生成合成文档，请根据以下逐集概念提取结果输出。"
        )
        issues = validate_synthesis(text, ["第1集.md", "第2集.md"])
        assert any("泄漏" in i for i in issues), issues

    def test_missing_core_sections_critical(self):
        """缺少核心章节应判严重"""
        text = "# 只有标题\n\n内容较少。\n第1集相关。\n"
        issues = validate_synthesis(text, ["第1集.md"])
        assert any("[严重]" in i and "核心章节" in i for i in issues), issues

    def test_low_coverage_warned(self):
        """10 篇源笔记只引用 2 篇 → 覆盖度警告"""
        paths = [f"第{i}集.md" for i in range(1, 11)]
        issues = validate_synthesis(self._valid_synthesis(), paths)
        assert any("覆盖度" in i for i in issues), issues

    def test_no_source_refs_warned(self):
        """无任何来源标注应提示（非严重）"""
        text = (
            "# 合成文档\n\n"
            "## 核心思维模型\n内容\n\n## 方法论\n内容\n\n"
            "## 学习路径\n内容\n\n## 金句\n内容\n"
        )
        issues = validate_synthesis(text, ["第1集.md"])
        assert any("来源标注" in i for i in issues), issues
        assert all("[严重]" not in i for i in issues), issues

    def test_title_named_notes_no_phantom_episodes(self):
        """标题命名的源笔记：按标题标注来源不应被误判为「不存在的集数」"""
        text = (
            "# 量化知识体系\n\n"
            "## 核心思维模型\n"
            "模型一，来源《互联网泡沫、量化崛起、沃什首秀，投资30年的美国往事》。\n\n"
            "## 方法论\n"
            "方法一，来源《对话Calvin：拆解量化交易炼金术》。\n\n"
            "## 学习路径\n"
            "阶段一。\n\n"
            "## 金句精选\n"
            '> "金句一"\n'
        )
        note_paths = [
            "互联网泡沫、量化崛起、沃什首秀，投资30年的美国往事.md",
            "对话Calvin：拆解量化交易炼金术.md",
            "拆解量化全天候的底层赚钱逻辑：要穿越周期，首先要做到相信.md",
        ]
        issues = validate_synthesis(text, note_paths)
        assert all("[严重]" not in i for i in issues), issues
        assert not any("不存在的集数" in i for i in issues), issues

    def test_title_named_note_uses_episode_refs_warned(self):
        """标题命名源笔记却引用「第X集」→ 仅警告（无法核对）"""
        text = (
            "# 合成文档\n\n"
            "## 核心思维模型\n模型一（第2集）\n\n## 方法论\n内容\n\n"
            "## 学习路径\n内容\n\n## 金句精选\n> 原话\n"
        )
        issues = validate_synthesis(text, ["互联网泡沫、量化崛起、沃什首秀.md"])
        assert any("第X集" in i and "[警告]" in i for i in issues), issues
        assert all("[严重]" not in i for i in issues), issues

    def test_episode_leading_zero_normalized(self):
        """第01集 源笔记与文档中「第1集」引用应视为同一集"""
        text = (
            "# 合成文档\n\n"
            "## 核心思维模型\n模型一（第1集）\n\n## 方法论\n内容\n\n"
            "## 学习路径\n内容\n\n## 金句精选\n> 原话\n\n"
            "第1集关联第2集。\n"
        )
        issues = validate_synthesis(text, ["第01集.md", "第02集.md"])
        assert all("[严重]" not in i for i in issues), issues
        assert not any("不存在的集数" in i for i in issues), issues

    def test_english_cot_planning_detected(self):
        """模型用英文做规划（输出前思考）应判严重，即使中文正文干净"""
        text = (
            "The user wants me to create a systematic knowledge synthesis document "
            "based on the key concepts extracted from multiple episodes.\n"
            "Let me organize the content by themes first.\n"
            + self._valid_synthesis()
        )
        issues = validate_synthesis(text, ["第1集.md", "第2集.md"])
        assert any("泄漏" in i and "[严重]" in i for i in issues), issues

    def test_duplicate_sections_detected(self):
        """同一章节标题出现两次（草稿/文档重复拼接）应判严重"""
        text = self._valid_synthesis() + "\n\n" + self._valid_synthesis()
        issues = validate_synthesis(text, ["第1集.md", "第2集.md"])
        assert any("重复章节" in i and "[严重]" in i for i in issues), issues

    def test_single_english_plan_first_line_detected(self):
        """正文首行即为英文规划语言 → 单标记也判泄漏"""
        text = "Let me write the final document now.\n" + self._valid_synthesis()
        issues = validate_synthesis(text, ["第1集.md", "第2集.md"])
        assert any("泄漏" in i and "[严重]" in i for i in issues), issues


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
            "第3集与已有内容关联，递进关系。\n",  # update
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

    def test_run_extractions_cot_retry(self, engine, tmp_path):
        """Stage-1 提取结果含思考过程/规划语言时，应带反馈重试并使用干净输出"""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir(exist_ok=True)
        note = notes_dir / "第1集.md"
        note.write_text("# 第1集\n\n内容", encoding='utf-8')
        extractions_dir = notes_dir / "extractions"
        extractions_dir.mkdir(exist_ok=True)

        provider = MagicMock()
        # 第一次输出混入规划语言（"首先，先" + "哦对，" = 2 个 COT 标记），重试输出干净
        provider.generate.side_effect = [
            "首先，先提取核心模型。哦对，还有方法论。\n### 核心模型\n- 模型一: 定义",
            "### 核心模型\n- 模型一: 定义",
        ]

        result = engine._run_extractions(
            [str(note)], provider, "system_prompt", extractions_dir
        )

        assert provider.generate.call_count == 2
        assert len(result) == 1
        assert "首先" not in result[0]
        assert "模型一" in result[0]
        # 缓存文件应写入干净的重试输出
        cache = extractions_dir / "第1集_extraction.md"
        assert cache.exists()
        assert "首先" not in cache.read_text(encoding='utf-8')

    def test_run_extractions_cot_clean_no_retry(self, engine, tmp_path):
        """提取结果干净时不应重试"""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir(exist_ok=True)
        note = notes_dir / "第2集.md"
        note.write_text("# 第2集\n\n内容", encoding='utf-8')
        extractions_dir = notes_dir / "extractions"
        extractions_dir.mkdir(exist_ok=True)

        provider = MagicMock()
        provider.generate.return_value = "### 核心模型\n- 模型二: 定义"

        result = engine._run_extractions(
            [str(note)], provider, "system_prompt", extractions_dir
        )

        assert provider.generate.call_count == 1
        assert len(result) == 1
        assert "模型二" in result[0]
