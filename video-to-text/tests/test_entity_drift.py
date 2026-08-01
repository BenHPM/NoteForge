# -*- coding: utf-8 -*-
"""
跨块实体连续性验证测试

覆盖：
  - _check_entity_drift: 人名漂移检测
  - _check_entity_drift: 术语连续性检测
  - 正常情况不误报
  - 空输入处理
"""

import pytest
from noteforge.engine.stages.generate import GenerateStage


class TestEntityDrift:
    """_check_entity_drift 实体漂移检测测试"""

    def test_no_drift_no_issues(self):
        """正常连续的两块不应报问题"""
        prev = (
            "## 核心观点\n\n"
            "张三指出，市场趋势向好。\n"
            "李四认为，投资需谨慎。\n"
        )
        curr = (
            "## 核心观点\n\n"
            "张三进一步分析了市场趋势。\n"
            "王五补充了新的观点。\n"
        )
        issues = GenerateStage._check_entity_drift(prev, curr)
        # 张三延续，李四消失但只有1个，不应报
        assert len(issues) == 0

    def test_person_name_drift_detected(self):
        """多个人名消失应被检测（部分消失，非全部）"""
        prev = (
            "张三指出，市场趋势向好。\n"
            "李四认为，投资需谨慎。\n"
            "王五分析了风险。\n"
            "赵六强调了合规。\n"
        )
        # 张三直接跟动词（指出/表示），能被模式匹配到
        curr = (
            "张三指出，市场趋势延续向好。\n"
            "孙七表示了不同意见。\n"
        )
        issues = GenerateStage._check_entity_drift(prev, curr)
        # 张三延续（张三指出），李四/王五/赵六消失（3个，超过阈值 2）
        assert len(issues) > 0
        assert any("人名" in issue for issue in issues)

    def test_term_continuity_check(self):
        """前块术语在后块未延续应被检测"""
        prev = (
            "## 量化投资策略\n\n"
            "## 因子模型分析\n\n"
            "## 风险管理框架\n\n"
            "## 回测方法论\n\n"
        )
        curr = (
            "## 完全不相关的内容\n\n"
            "这是另一块完全不同的内容，没有延续前块的任何术语。\n"
        )
        issues = GenerateStage._check_entity_drift(prev, curr)
        # 3 个术语未延续（超过阈值 2）
        assert len(issues) > 0
        assert any("术语" in issue for issue in issues)

    def test_generic_terms_not_flagged(self):
        """通用标题不应触发术语漂移"""
        prev = (
            "## 核心观点\n\n"
            "内容...\n"
        )
        curr = (
            "## 学习总结\n\n"
            "其他内容...\n"
        )
        issues = GenerateStage._check_entity_drift(prev, curr)
        # 核心观点和学习总结是通用标题，不应报
        assert not any("术语" in issue for issue in issues)

    def test_empty_chunks_no_error(self):
        """空输入不应报错"""
        issues = GenerateStage._check_entity_drift("", "")
        assert issues == []

    def test_no_person_names_no_drift(self):
        """无人名时不应报漂移"""
        prev = "这是第一块内容，没有具体人名。\n"
        curr = "这是第二块内容，也没有具体人名。\n"
        issues = GenerateStage._check_entity_drift(prev, curr)
        assert not any("人名" in issue for issue in issues)

    def test_non_person_words_filtered(self):
        """非人名词（如'分析'、'策略'）不应被当作人名"""
        prev = (
            "分析认为市场向好。\n"
            "策略指出需要调整。\n"
        )
        curr = (
            "框架提出了新视角。\n"
        )
        issues = GenerateStage._check_entity_drift(prev, curr)
        # '分析'、'策略'、'框架' 都在 _non_person_words 中
        assert not any("人名" in issue for issue in issues)

    def test_partial_name_continuation_ok(self):
        """人名部分延续（如张三在两块都出现）不应报漂移"""
        prev = (
            "张三指出市场趋势。\n"
            "李四认为需谨慎。\n"
            "王五分析了风险。\n"
        )
        curr = (
            "张三进一步分析了市场趋势。\n"
            "赵六补充了新观点。\n"
        )
        issues = GenerateStage._check_entity_drift(prev, curr)
        # 张三延续了，李四和王五消失（2个，刚好不超过阈值 2）
        assert not any("人名" in issue for issue in issues)
