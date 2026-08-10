# -*- coding: utf-8 -*-
import os
import pytest
from noteforge.quality.rules import (
    check_insight_actionability,
    check_layering_accuracy,
    check_timeline_accuracy,
    check_quote_attribution,
)
from noteforge.quality.rules_factual import (
    check_fabricated_data,
    check_unmarked_additions,
    check_semantic_reversal,
    check_name_number_consistency,
    num_to_chinese,
    number_in_source,
)
from noteforge.quality.models import Issue, RuleResult

# ============================================================
# num_to_chinese
# ============================================================

class TestNumToChinese:
    """数字转中文辅助函数"""

    def test_25(self):
        assert num_to_chinese("25") == "二十五"

    def test_10(self):
        assert num_to_chinese("10") == "十"

    def test_3_point_5(self):
        assert num_to_chinese("3.5") == "三点五"

    def test_100(self):
        assert num_to_chinese("100") == "一百"

    def test_0(self):
        assert num_to_chinese("0") == "零"

    def test_invalid_input(self):
        assert num_to_chinese("abc") == ""


# ============================================================
# number_in_source
# ============================================================

class TestNumberInSource:
    """智能数字匹配"""

    def test_exact_match(self):
        """精确匹配 '25%' in source"""
        assert number_in_source("25%", "增长率为25%") is True

    def test_chinese_percent(self):
        """'百分之二十五' 匹配 '25%'"""
        assert number_in_source("25%", "增长率为百分之二十五") is True

    def test_approx_prefix(self):
        """'约25%' 匹配 '25%'"""
        assert number_in_source("约25%", "增长率为25%") is True

    def test_no_match(self):
        """无匹配 → False"""
        assert number_in_source("99%", "增长率为25%") is False


# ============================================================
# R1 check_fabricated_data
# ============================================================

class TestR1FabricatedData:
    """R1: 禁止虚构数据"""

    def test_number_not_in_source(self):
        """数字不在原文中 → fatal issue"""
        note = "市场占有率达到75%"
        source = "市场占有率约为25%"
        result = check_fabricated_data([r'[\d.]+\s*%'], note, source)
        assert not result.passed
        assert any(i.severity == "fatal" for i in result.issues)

    def test_number_in_source(self):
        """数字在原文中 → 通过"""
        note = "市场占有率达到25%"
        source = "市场占有率约为25%"
        result = check_fabricated_data([r'[\d.]+\s*%'], note, source)
        assert result.passed
        assert len(result.issues) == 0

    def test_timestamp_skipped(self):
        """时间戳格式跳过"""
        note = "在2:30处提到"
        source = "一些内容"
        result = check_fabricated_data([r'[\d.]+\s*[：:]\s*\d+'], note, source)
        # 时间戳模式本身被匹配，但 check_fabricated_data 内部会跳过
        # 用百分比模式测试：笔记中只有时间戳，无百分比
        result2 = check_fabricated_data([r'[\d.]+\s*%'], note, source)
        assert result2.passed


# ============================================================
# R2 check_unmarked_additions
# ============================================================

class TestR2UnmarkedAdditions:
    """R2: 禁止越界增补"""

    def test_strategy_without_mark(self):
        """有策略建议但无 [📝笔者补充] 标记 → fatal issue"""
        note = "✅ 短期应对策略：增加仓位"
        source = "今天天气不错"
        result = check_unmarked_additions(note, source)
        assert not result.passed
        assert any(i.severity == "fatal" for i in result.issues)

    def test_with_mark(self):
        """有标记 → 通过"""
        note = "✅ 短期应对策略：增加仓位 [📝笔者补充]"
        source = "今天天气不错"
        result = check_unmarked_additions(note, source)
        assert result.passed

    def test_no_strategy(self):
        """无策略建议 → 通过"""
        note = "这是一个关于投资的笔记"
        source = "这是一个关于投资的内容"
        result = check_unmarked_additions(note, source)
        assert result.passed


# ============================================================
# R3 check_semantic_reversal
# ============================================================

class TestR3SemanticReversal:
    """R3: 禁止事实反转

    2026-08-10 修复后的语义：反转 = 笔记断言某一极性，且原文明确断言相反极性。
    双方极性必须在场且互相矛盾才判定；原文缺失相反表述 ≠ 反转
    （笔记作独立合理总结时，原文不需要出现相反词）。
    """

    def test_reversal_pattern(self):
        """语义反转 → issue（笔记断言'可解释性强'，原文明确断言'不可解释'）"""
        note = "这个模型可解释性强"
        source = "这个模型不可解释，是个黑箱"  # 原文明确相反方向
        result = check_semantic_reversal(note, source)
        assert not result.passed
        assert len(result.issues) > 0

    def test_no_reversal(self):
        """原文与笔记同向 → 不标记（原文确认了可解释方向，非反转）"""
        note = "这个模型可解释性强"
        source = "这个模型可解释性强，透明"
        result = check_semantic_reversal(note, source)
        assert result.passed


# ============================================================
# R8 check_insight_actionability
# ============================================================

class TestR8InsightActionability:
    """R8: 洞察可行动性"""

    def test_vague_summary(self):
        """空洞总结 → major issue"""
        note = "## 洞察\n要重视风险管理\n"
        result = check_insight_actionability(note)
        assert not result.passed
        assert any(i.severity == "major" for i in result.issues)

    def test_concrete_action(self):
        """有具体行动动词 → 通过"""
        note = "## 洞察\n要重视风险管理，执行每日检查清单，记录异常指标\n"
        result = check_insight_actionability(note)
        assert result.passed

    def test_no_insight_section(self):
        """无洞察段落 → 通过"""
        note = "## 核心观点\n投资需要耐心\n"
        result = check_insight_actionability(note)
        assert result.passed


# ============================================================
# R9 check_layering_accuracy
# ============================================================

class TestR9LayeringAccuracy:
    """R9: 分层准确性"""

    def test_over_generalization(self):
        """过度泛化 → medium issue"""
        note = "所有创作者都应该重视内容质量"
        result = check_layering_accuracy(note)
        assert not result.passed
        assert any(i.severity == "medium" for i in result.issues)

    def test_in_quote_context(self):
        """在引用上下文中的泛化 = 说话人原话，不判为笔记过度泛化

        2026-08-10 6h 访谈实测：忠实引用嘉宾原话（"每个人都应该…"）原实现
        降级 medium 仍扣分，7 条打穿 R9 分数 → 门禁拒绝整篇忠实笔记。
        引用保留是正确行为，不再计入 R9 问题。
        """
        note = "「所有创作者都应该重视内容质量」"
        result = check_layering_accuracy(note)
        assert result.passed
        assert len(result.issues) == 0

    def test_with_scope_marker(self):
        """有适用范围标注 → 通过"""
        note = "在短视频场景中，所有创作者都应该重视内容质量"
        result = check_layering_accuracy(note)
        assert result.passed


# ============================================================
# R10 check_timeline_accuracy
# ============================================================

class TestR10TimelineAccuracy:
    """R10: 时间线准确性"""

    def test_note_has_timeline_source_does_not(self):
        """笔记有时序但原文没有 → medium issue"""
        # 行必须 >=60 字且不以 # 开头才会被检测
        note = "首先需要深入了解宏观经济基本面和行业趋势，然后进行系统性的技术分析和量化验证，最后执行严格的交易决策和风险控制，确保投资组合的安全性和收益性"
        source = "宏观经济基本面技术分析交易决策风险控制"
        result = check_timeline_accuracy(note, source)
        assert not result.passed
        assert any(i.severity == "medium" for i in result.issues)

    def test_source_has_timeline(self):
        """原文也有时序词 → 通过"""
        note = "首先需要了解基本面，然后进行技术分析"
        source = "首先看基本面，然后做技术分析"
        result = check_timeline_accuracy(note, source)
        assert result.passed

    def test_heading_timeline_skipped(self):
        """标题行中的时序词跳过"""
        note = "## 首先…然后…\n这是正文内容，没有时序关系描述"
        source = "一些没有时序的内容"
        result = check_timeline_accuracy(note, source)
        assert result.passed


# ============================================================
# R11 check_quote_attribution
# ============================================================

class TestR11QuoteAttribution:
    """R11: 引用归属"""

    def test_name_not_in_source(self):
        """人名不在原文中 → major issue"""
        note = "张三指出：市场前景广阔"
        source = "李四说市场前景广阔"
        result = check_quote_attribution(note, source)
        assert not result.passed
        assert any(i.severity == "major" for i in result.issues)

    def test_name_in_source(self):
        """人名在原文中 → 通过"""
        note = "李四指出：市场前景广阔"
        source = "李四说市场前景广阔"
        result = check_quote_attribution(note, source)
        assert result.passed

    def test_asr_fuzzy_match(self):
        """ASR 同音字匹配 → medium issue（容差）"""
        note = "狄东升认为：人民币国际化加速"
        source = "翟东升说人民币国际化加速"
        result = check_quote_attribution(note, source)
        assert not result.passed
        assert any(i.severity == "medium" for i in result.issues)

    def test_non_person_excluded(self):
        """非人名词排除"""
        note = "因此认为：市场前景广阔"
        source = "一些内容"
        result = check_quote_attribution(note, source)
        assert result.passed


# ============================================================
# R12 check_name_number_consistency
# ============================================================

class TestR12NameNumberConsistency:
    """R12: 人名/数字一致性"""

    def test_name_not_in_source(self):
        """人名不在原文 → issue"""
        note = "赵六认为市场会上涨"
        source = "李四说市场会上涨"
        result = check_name_number_consistency(note, source)
        assert not result.passed
        assert len(result.issues) > 0

    def test_percentage_not_in_source(self):
        """百分比不在原文 → issue"""
        note = "增长率为88%"
        source = "增长率为25%"
        result = check_name_number_consistency(note, source)
        assert not result.passed
        assert any("88%" in i.description for i in result.issues)

    def test_all_consistent(self):
        """都一致 → 通过"""
        note = "李四认为市场会上涨，增长率为25%"
        source = "李四说市场会上涨，增长率为25%"
        result = check_name_number_consistency(note, source)
        assert result.passed
