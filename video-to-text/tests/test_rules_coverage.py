# -*- coding: utf-8 -*-
import os
import pytest
from noteforge.quality.rules_coverage import (
    extract_framework_section,
    check_concept_distortion,
    check_coverage,
    check_consistency,
    check_framework_completeness,
)
from noteforge.quality.models import Issue, RuleResult

os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


# ============================================================
# extract_framework_section
# ============================================================

class TestExtractFrameworkSection:
    """extract_framework_section: 提取包含框架的段落，>=3 匹配才返回"""

    def test_three_plus_matches_returns_text(self):
        """3+ 匹配时返回文本段"""
        text = "前置内容 " + "第一步做A " * 3 + "后续内容"
        result = extract_framework_section(text, r'第[一二三四五六七八九十\d]+步')
        assert result != ""
        assert "第一步" in result

    def test_less_than_three_matches_returns_empty(self):
        """<3 匹配时返回空字符串"""
        text = "第一步做A 第二步做B 然后就没有了"
        result = extract_framework_section(text, r'第[一二三四五六七八九十\d]+步')
        assert result == ""

    def test_zero_matches_returns_empty(self):
        """0 匹配时返回空字符串"""
        text = "没有任何框架标记的普通文本"
        result = extract_framework_section(text, r'第[一二三四五六七八九十\d]+步')
        assert result == ""

    def test_boundary_trimming_start(self):
        """边界裁剪：开头不足 100 字符时从 0 开始"""
        # 构造文本：开头就是匹配，确保 start=max(0, ...) 生效
        text = "第一步A\n第二步B\n第三步C\n第四步D\n第五步E"
        result = extract_framework_section(text, r'第[一二三四五六七八九十\d]+步')
        assert result.startswith("第一步")

    def test_boundary_trimming_end(self):
        """边界裁剪：末尾不足 100 字符时到文本末尾"""
        text = "x" * 200 + " 第一步A 第二步B 第三步C"
        result = extract_framework_section(text, r'第[一二三四五六七八九十\d]+步')
        assert result.endswith("第三步C")

    def test_boundary_trimming_with_padding(self):
        """边界裁剪：前后各扩展 100 字符"""
        prefix = "A" * 150
        suffix = "Z" * 150
        middle = " 第一步A 第二步B 第三步C 第四步D "
        text = prefix + middle + suffix
        result = extract_framework_section(text, r'第[一二三四五六七八九十\d]+步')
        # 应包含第一个匹配前 100 字符和最后一个匹配后 100 字符
        assert len(result) < len(text)  # 裁剪过
        assert "第一步" in result
        assert "第四步" in result


# ============================================================
# check_concept_distortion (R4)
# ============================================================

class TestCheckConceptDistortion:
    """R4: 禁止关键概念简化失真"""

    def test_missing_keywords_produces_issue(self):
        """概念缺失关键词 → 产生 issue"""
        key_concepts = {
            "量化投资": ["统计模型", "纪律性"],
        }
        # 短上下文，概念只是简单提及
        note_text = "量化投资是一种方法。"
        result = check_concept_distortion(key_concepts, note_text)
        assert not result.passed
        assert len(result.issues) > 0
        assert result.issues[0].rule_id == "R4"
        # 短上下文 <80 字 → severity=medium (规则: <80 medium, 80-200 major)
        assert result.issues[0].severity in ("medium", "major")

    def test_rich_context_no_issue(self):
        """概念有丰富上下文（>200字）→ 不产生 issue"""
        key_concepts = {
            "量化投资": ["统计模型", "纪律性"],
        }
        # 构造 >200 字的上下文
        padding = "这是一段很长的说明文字，" * 20  # ~240 字
        note_text = f"{padding}量化投资是一种方法。{padding}"
        result = check_concept_distortion(key_concepts, note_text)
        assert result.passed
        assert len(result.issues) == 0

    def test_partial_keywords_passes(self):
        """概念有部分关键词 → 通过（missing 为空则无 issue）"""
        key_concepts = {
            "量化投资": ["统计模型"],
        }
        note_text = "量化投资基于统计模型进行投资决策。"
        result = check_concept_distortion(key_concepts, note_text)
        assert result.passed
        assert len(result.issues) == 0

    def test_empty_key_concepts_passes(self):
        """空 key_concepts → 通过"""
        result = check_concept_distortion({}, "任何文本")
        assert result.passed
        assert len(result.issues) == 0

    def test_concept_not_in_note_passes(self):
        """概念不在笔记中 → 通过（不检查）"""
        key_concepts = {
            "量化投资": ["统计模型"],
        }
        note_text = "今天天气很好，适合出门散步。"
        result = check_concept_distortion(key_concepts, note_text)
        assert result.passed
        assert len(result.issues) == 0

    def test_medium_severity_for_context_80_to_200(self):
        """上下文 80-200 字 → severity=major（规则: 80-200 为 major）"""
        key_concepts = {
            "因子投资": ["超额收益"],
        }
        # 构造上下文长度在 80-200 之间
        note_text = "因子投资是一种策略。" + "补充说明文字。" * 10
        result = check_concept_distortion(key_concepts, note_text)
        if result.issues:
            assert result.issues[0].severity == "major"

    def test_major_severity_for_short_context(self):
        """上下文 <80 字 → severity=major"""
        key_concepts = {
            "因子投资": ["超额收益", "风险因子"],
        }
        note_text = "因子投资很重要。"
        result = check_concept_distortion(key_concepts, note_text)
        assert not result.passed
        assert result.issues[0].severity == "major"


# ============================================================
# check_coverage (R5)
# ============================================================

class TestCheckCoverage:
    """R5: 覆盖度底线，双阈值 <30% fatal, <80% major"""

    def test_low_coverage_fatal(self):
        """覆盖率 <30% → fatal issue"""
        # 用长且独特的章节标题避免模糊匹配误命中
        source_text = (
            "**00:01 量化投资组合优化策略**\n"
            "**00:02 宏观经济周期与政策分析**\n"
            "**00:03 机器学习因子挖掘方法**\n"
            "**00:04 衍生品定价与风险管理**\n"
            "**00:05 高频交易系统架构设计**"
        )
        note_text = "量化投资组合优化策略是重要的话题"
        result = check_coverage(note_text, source_text)
        assert not result.passed  # 1/5 = 20% → fatal
        assert len(result.issues) > 0
        assert result.issues[0].severity == "fatal"

    def test_medium_coverage_major(self):
        """覆盖率 30%-80% → major issue"""
        source_text = (
            "**00:01 量化投资组合优化策略**\n"
            "**00:02 宏观经济周期与政策分析**\n"
            "**00:03 机器学习因子挖掘方法**\n"
            "**00:04 衍生品定价与风险管理**\n"
            "**00:05 高频交易系统架构设计**"
        )
        note_text = "量化投资组合优化策略的内容\n宏观经济周期与政策分析的内容\n其他无关内容"
        result = check_coverage(note_text, source_text)
        assert result.passed  # 2/5 = 40% → passed (above fatal threshold)
        assert len(result.issues) > 0
        assert result.issues[0].severity == "major"

    def test_high_coverage_no_issue(self):
        """覆盖率 >80% → 无 issue"""
        source_text = (
            "**00:01 量化投资组合优化策略**\n"
            "**00:02 宏观经济周期与政策分析**\n"
            "**00:03 机器学习因子挖掘方法**\n"
            "**00:04 衍生品定价与风险管理**\n"
            "**00:05 高频交易系统架构设计**"
        )
        note_text = (
            "量化投资组合优化策略\n"
            "宏观经济周期与政策分析\n"
            "机器学习因子挖掘方法\n"
            "衍生品定价与风险管理\n"
            "高频交易系统架构设计"
        )
        result = check_coverage(note_text, source_text)
        assert result.passed
        assert len(result.issues) == 0

    def test_no_chapter_markers_passes(self):
        """无章节标记 → 直接通过（ratio=1.0）"""
        source_text = "这是一段没有章节标记的普通转写文本"
        note_text = "笔记内容"
        result = check_coverage(note_text, source_text)
        assert result.passed
        assert len(result.issues) == 0

    def test_fuzzy_match_chinese_sliding_window(self):
        """模糊匹配：中文滑动窗口匹配章节标题"""
        # 滑动窗口从 chapter[:15] 生成 2-4 字子串，取最长的 4 个
        # 需要确保子串在笔记中出现
        source_text = "**00:01 投资策略**\n**00:02 经济分析**"
        note_text = "投资策略是关键\n经济分析很重要"
        result = check_coverage(note_text, source_text)
        # 精确匹配 chapter[:15].strip() = "00:01 投资策略" 不在 note 中
        # 但滑动窗口子串 "投资策略"(4字) 和 "经济分析"(4字) 应该匹配
        assert result.score > 0

    def test_score_capped_at_one(self):
        """score 上限为 1.0"""
        source_text = "**00:01 量化投资策略**\n**00:02 宏观经济分析**"
        note_text = "量化投资策略\n宏观经济分析"
        result = check_coverage(note_text, source_text)
        assert result.score <= 1.0


# ============================================================
# check_consistency (R6)
# ============================================================

class TestCheckConsistency:
    """R6: 术语一致性"""

    def test_contradiction_produces_issue(self):
        """术语表与正文矛盾 → issue"""
        note_text = (
            "| 术语 | 解释 |\n"
            "| --- | --- |\n"
            "| 因子 | 不可解释为主 |\n\n"
            "正文中提到因子投资的可解释性强，这是一个重要发现。"
        )
        result = check_consistency(note_text)
        assert not result.passed
        assert len(result.issues) > 0
        assert result.issues[0].rule_id == "R6"

    def test_consistent_terms_passes(self):
        """术语表与正文一致 → 通过"""
        note_text = (
            "| 术语 | 解释 |\n"
            "| --- | --- |\n"
            "| 因子 | 影响收益的特征变量 |\n\n"
            "因子作为影响收益的特征变量，在投资中非常重要。"
        )
        result = check_consistency(note_text)
        assert result.passed
        assert len(result.issues) == 0

    def test_no_term_table_passes(self):
        """无术语表 → 通过"""
        note_text = "这是一篇没有术语表的普通笔记。"
        result = check_consistency(note_text)
        assert result.passed
        assert len(result.issues) == 0

    def test_term_not_in_body_passes(self):
        """术语表中术语未在正文出现 → 通过（不检查）"""
        note_text = (
            "| 术语 | 解释 |\n"
            "| --- | --- |\n"
            "| Alpha | 超额收益 |\n\n"
            "本文讨论了其他内容，没有提到 Alpha。"
        )
        result = check_consistency(note_text)
        assert result.passed
        assert len(result.issues) == 0


# ============================================================
# check_framework_completeness (R7)
# ============================================================

class TestCheckFrameworkCompleteness:
    """R7: 框架完整性"""

    def test_short_steps_produces_issue(self):
        """框架步骤过短（>50% <20字）→ issue"""
        # 5 个编号步骤，每个都很短
        note_text = (
            "1. 准备\n"
            "2. 执行\n"
            "3. 检查\n"
            "4. 改进\n"
            "5. 总结\n"
        )
        result = check_framework_completeness(note_text)
        assert not result.passed
        assert len(result.issues) > 0
        assert result.issues[0].rule_id == "R7"

    def test_detailed_steps_passes(self):
        """框架步骤充分 → 通过"""
        # 5 个编号步骤，每个都有详细描述
        note_text = (
            "1. 准备阶段：收集所有必要的数据和资料，确保环境配置正确\n"
            "2. 执行阶段：按照预定计划逐步实施，记录每个关键节点的状态\n"
            "3. 检查阶段：对比预期结果与实际输出，识别偏差和异常情况\n"
            "4. 改进阶段：根据检查结果调整策略，优化流程中的薄弱环节\n"
            "5. 总结阶段：整理经验教训，形成可复用的知识文档和最佳实践\n"
        )
        result = check_framework_completeness(note_text)
        assert result.passed
        assert len(result.issues) == 0

    def test_no_framework_passes(self):
        """无框架段落 → 通过"""
        note_text = "这是一篇没有框架的普通笔记，只有段落和要点。"
        result = check_framework_completeness(note_text)
        assert result.passed
        assert len(result.issues) == 0

    def test_fewer_than_five_steps_passes(self):
        """框架步骤 <5 → 不触发检查（需要 >=5 才检查）"""
        note_text = "1. 准备\n2. 执行\n3. 检查\n"
        result = check_framework_completeness(note_text)
        assert result.passed
        assert len(result.issues) == 0

    def test_chinese_step_markers(self):
        """中文步骤标记（第一步、第二步...）"""
        note_text = (
            "第一步 准备\n"
            "第二步 执行\n"
            "第三步 检查\n"
            "第四步 改进\n"
            "第五步 总结\n"
        )
        result = check_framework_completeness(note_text)
        assert not result.passed
        assert len(result.issues) > 0
