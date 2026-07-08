# -*- coding: utf-8 -*-
"""
NoteForge 事实性质量规则单元测试

覆盖 noteforge/quality/rules_factual.py 的 R1/R2/R3/R12 规则和辅助函数。

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_rules_factual.py -v
"""
import os
import pytest

# 跳过 env_check
os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


# ============================================================
# 辅助函数测试
# ============================================================

class TestNumToChinese:
    """num_to_chinese 数字转中文测试"""

    def test_num_to_chinese_basic(self):
        """基本数字 0-10 转中文"""
        from noteforge.quality.rules_factual import num_to_chinese
        assert num_to_chinese("0") == "零"
        assert num_to_chinese("1") == "一"
        assert num_to_chinese("5") == "五"
        assert num_to_chinese("10") == "十"

    def test_num_to_chinese_tens(self):
        """整十数转中文"""
        from noteforge.quality.rules_factual import num_to_chinese
        assert num_to_chinese("20") == "二十"
        assert num_to_chinese("50") == "五十"
        assert num_to_chinese("90") == "九十"
        assert num_to_chinese("100") == "一百"

    def test_num_to_chinese_compound(self):
        """非整十数转中文（如 23 → 二十三）"""
        from noteforge.quality.rules_factual import num_to_chinese
        assert num_to_chinese("23") == "二十三"
        assert num_to_chinese("15") == "十五"
        assert num_to_chinese("99") == "九十九"

    def test_num_to_chinese_decimal(self):
        """一位小数转中文"""
        from noteforge.quality.rules_factual import num_to_chinese
        assert num_to_chinese("3.5") == "三点五"
        assert num_to_chinese("1.2") == "一点二"

    def test_num_to_chinese_over_100(self):
        """超过 100 不转换，返回原数字字符串"""
        from noteforge.quality.rules_factual import num_to_chinese
        assert num_to_chinese("150") == "150"

    def test_num_to_chinese_invalid(self):
        """无效输入返回空字符串"""
        from noteforge.quality.rules_factual import num_to_chinese
        assert num_to_chinese("abc") == ""
        assert num_to_chinese("") == ""


class TestNumberInSource:
    """number_in_source 智能数字匹配测试"""

    def test_number_in_source_exact_match(self):
        """精确匹配：数字直接出现在原文中"""
        from noteforge.quality.rules_factual import number_in_source
        assert number_in_source("25%", "增长率为25%") is True

    def test_number_in_source_no_space_variant(self):
        """去空格变体匹配：25% vs 25 %"""
        from noteforge.quality.rules_factual import number_in_source
        assert number_in_source("25%", "增长率为 25 %") is True

    def test_number_in_source_chinese_percent(self):
        """中文百分比匹配：25% vs 百分之二十五"""
        from noteforge.quality.rules_factual import number_in_source
        assert number_in_source("25%", "增长了百分之二十五") is True

    def test_number_in_source_chinese_points(self):
        """中文百分点匹配：25% vs 二十五个百分点"""
        from noteforge.quality.rules_factual import number_in_source
        assert number_in_source("25%", "上升了二十五个百分点") is True

    def test_number_in_source_approx_prefix(self):
        """近似前缀匹配：约25% vs 25%"""
        from noteforge.quality.rules_factual import number_in_source
        assert number_in_source("约25%", "增长率为25%") is True

    def test_number_in_source_pure_number(self):
        """纯数字匹配：25 在原文中出现"""
        from noteforge.quality.rules_factual import number_in_source
        assert number_in_source("25%", "第25集") is True

    def test_number_in_source_not_found(self):
        """数字在原文中完全不存在"""
        from noteforge.quality.rules_factual import number_in_source
        assert number_in_source("99%", "增长率为25%") is False

    def test_number_in_source_no_numeric(self):
        """表达式无数字时返回 False"""
        from noteforge.quality.rules_factual import number_in_source
        assert number_in_source("abc", "任意文本") is False


# ============================================================
# R1: 禁止虚构数据
# ============================================================

class TestCheckFabricatedData:
    """R1 禁止虚构数据测试"""

    def test_check_fabricated_data_no_issues(self):
        """原文包含所有数字时无问题"""
        from noteforge.quality.rules_factual import check_fabricated_data
        source = "增长率为25%，用户数达到300万"
        note = "增长率为25%，用户数达到300万"
        result = check_fabricated_data(
            [r'[\d.]+\s*%?', r'\d+万'],
            note, source,
        )
        assert result.passed is True
        assert result.score == 1.0
        assert len(result.issues) == 0

    def test_check_fabricated_data_has_issues(self):
        """原文不包含数字时有问题"""
        from noteforge.quality.rules_factual import check_fabricated_data
        source = "增长率有所提升"
        note = "增长率为25%"
        result = check_fabricated_data(
            [r'[\d.]+\s*%'],
            note, source,
        )
        assert result.passed is False
        assert len(result.issues) > 0
        assert result.issues[0].rule_id == "R1"

    def test_check_fabricated_data_skips_timestamps(self):
        """时间戳格式不报错"""
        from noteforge.quality.rules_factual import check_fabricated_data
        source = "讨论了相关话题"
        note = "在2:30处讨论了相关话题"
        result = check_fabricated_data(
            [r'[\d.]+\s*[：:]?\s*\d*'],
            note, source,
        )
        # 时间戳格式应被跳过
        timestamp_issues = [i for i in result.issues if ':' in i.description or '：' in i.description]
        # 时间戳不应被标记为虚构数据
        assert result.passed is True or len(result.issues) == 0

    def test_check_fabricated_data_skips_quote_numbers(self):
        """Markdown 引用中紧邻 > 的数字不报错"""
        from noteforge.quality.rules_factual import check_fabricated_data
        source = "原文提到相关内容"
        # > 紧邻数字时，ctx_prefix.rstrip().endswith('>') 为 True
        note = ">25%"
        result = check_fabricated_data(
            [r'[\d.]+\s*%'],
            note, source,
        )
        # 引用中紧邻 > 的数字应被跳过
        assert result.passed is True or len(result.issues) == 0


# ============================================================
# R2: 禁止越界增补
# ============================================================

class TestCheckUnmarkedAdditions:
    """R2 禁止越界增补测试"""

    def test_check_unmarked_additions_marked(self):
        """有 [📝笔者补充] 标记时无问题"""
        from noteforge.quality.rules_factual import check_unmarked_additions
        source = "讨论了市场趋势"
        note = "✅ 短期应对策略建议 [📝笔者补充]\n\n其他内容"
        result = check_unmarked_additions(note, source)
        # 有标记时不应报 R2 问题
        assert result.passed is True
        assert len(result.issues) == 0

    def test_check_unmarked_additions_unmarked(self):
        """无标记的增补内容应报问题"""
        from noteforge.quality.rules_factual import check_unmarked_additions
        source = "讨论了市场趋势"
        note = "✅ 短期应对策略建议采取行动"
        result = check_unmarked_additions(note, source)
        assert result.passed is False
        assert len(result.issues) > 0
        assert result.issues[0].rule_id == "R2"

    def test_check_unmarked_additions_no_suspicion(self):
        """无增补嫌疑内容时通过"""
        from noteforge.quality.rules_factual import check_unmarked_additions
        source = "讨论了市场趋势和投资策略"
        note = "本文讨论了市场趋势和投资策略"
        result = check_unmarked_additions(note, source)
        assert result.passed is True
        assert len(result.issues) == 0


# ============================================================
# R3: 禁止事实反转
# ============================================================

class TestCheckSemanticReversal:
    """R3 禁止事实反转测试"""

    def test_check_semantic_reversal_no_reversal(self):
        """无反转模式时通过"""
        from noteforge.quality.rules_factual import check_semantic_reversal
        source = "讨论了投资策略"
        note = "本文讨论了投资策略"
        result = check_semantic_reversal(note, source)
        assert result.passed is True
        assert len(result.issues) == 0

    def test_check_semantic_reversal_detected(self):
        """检测到反转模式时报问题"""
        from noteforge.quality.rules_factual import check_semantic_reversal
        source = "模型训练过程复杂"
        note = "可解释性强，模型透明"
        result = check_semantic_reversal(note, source)
        assert result.passed is False
        assert len(result.issues) > 0
        assert result.issues[0].rule_id == "R3"

    def test_check_semantic_reversal_with_supporting_source(self):
        """原文有对应表述时不报反转"""
        from noteforge.quality.rules_factual import check_semantic_reversal
        # 反转模式: ("不是黑箱|非黑箱", "可解释性[强高好]", "fatal")
        # 笔记出现 "不是黑箱" 时，原文需包含 "可解释性强/高/好" 才不报反转
        source = "可解释性强，模型透明"
        note = "不是黑箱"
        result = check_semantic_reversal(note, source)
        # 原文有 "可解释性强" 匹配 "可解释性[强高好]"，不应报反转
        assert result.passed is True


# ============================================================
# R12: 人名/数字一致性
# ============================================================

class TestCheckNameNumberConsistency:
    """R12 人名/数字一致性测试"""

    def test_check_name_number_consistency_names_match(self):
        """人名在原文中存在时无问题"""
        from noteforge.quality.rules_factual import check_name_number_consistency
        source = "翟东升指出中美关系的变化"
        note = "翟东升认为中美关系正在变化"
        result = check_name_number_consistency(note, source)
        # 翟东升在原文中存在
        name_issues = [i for i in result.issues if "翟东升" in i.description]
        assert len(name_issues) == 0

    def test_check_name_number_consistency_name_not_in_source(self):
        """人名不在原文中时报问题"""
        from noteforge.quality.rules_factual import check_name_number_consistency
        source = "讨论了相关话题"
        note = "张三指出市场趋势"
        result = check_name_number_consistency(note, source)
        assert result.passed is False
        name_issues = [i for i in result.issues if "张三" in i.description]
        assert len(name_issues) > 0

    def test_check_name_number_consistency_fuzzy_match(self):
        """同音/形近字模糊匹配 — 应标记为 ASR 容差而非真正错误"""
        from noteforge.quality.rules_factual import check_name_number_consistency
        source = "狄马指出市场趋势"
        note = "翟马认为市场趋势向好"
        result = check_name_number_consistency(note, source)
        # 翟→狄 模糊匹配成功，应产生 ASR 容差标记（severity=low），而非 major 错误
        name_issues = [i for i in result.issues if "翟马" in i.description]
        assert len(name_issues) == 1
        assert name_issues[0].severity == "low"
        assert "ASR" in name_issues[0].description

    def test_check_name_number_consistency_non_person_filtered(self):
        """非人名词汇（如'讲师'）应被过滤"""
        from noteforge.quality.rules_factual import check_name_number_consistency
        source = "讨论了相关话题"
        note = "讲师指出市场趋势"
        result = check_name_number_consistency(note, source)
        name_issues = [i for i in result.issues if "讲师" in i.description]
        assert len(name_issues) == 0

    def test_check_name_number_consistency_number_mismatch(self):
        """数字在原文中不存在时报问题"""
        from noteforge.quality.rules_factual import check_name_number_consistency
        source = "增长有所提升"
        note = "增长率为99%"
        result = check_name_number_consistency(note, source)
        num_issues = [i for i in result.issues if "99%" in i.description]
        assert len(num_issues) > 0
