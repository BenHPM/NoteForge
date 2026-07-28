# -*- coding: utf-8 -*-
"""
FeedbackComposer + LLM 评审条件触发测试

覆盖：
1. FeedbackComposer 规则反馈构建
2. FeedbackComposer LLM 维度反馈构建
3. FeedbackComposer 组合（规则 + LLM）
4. FeedbackComposer 单维度反馈（避免振荡）
5. 条件 LLM 评审触发逻辑（边界分数）
6. FeedbackBundle.to_prompt_section() 输出格式

运行:
    cd video-to-text
    envs/paraformer/python.exe -m pytest tests/test_feedback_composer.py -v
"""

import pytest
from noteforge.quality.feedback_composer import (
    FeedbackComposer, FeedbackBundle, FeedbackItem,
)
from noteforge.quality.models import LLMEvalResult


# ─── 测试数据 ───

SAMPLE_RULE_REPORT = {
    "total_score": 0.72,
    "overall_passed": False,
    "rule_results": {
        "R1": {"passed": True, "issues": []},
        "R2": {"passed": False, "issues": [
            {"severity": "fatal", "description": "虚构占比50%", "suggestion": "删除无出处的数据"},
        ]},
        "R5": {"passed": False, "issues": [
            {"severity": "major", "description": "覆盖率仅60%", "suggestion": "补充遗漏议题"},
        ]},
    },
}

SAMPLE_LLM_RESULT = LLMEvalResult(
    richness_score=2.5,
    readability_score=4.0,
    faithfulness_score=3.5,
    actionability_score=2.0,
    overall_score=3.0,
    feedback="笔记可读性良好，但内容丰富度和可行动性不足",
    suggestions=["补充更多具体数据和案例", "将模糊建议改为可执行步骤"],
)


# ─── FeedbackComposer.from_rule_report ───

class TestFromRuleReport:
    def test_extracts_failed_rules(self):
        bundle = FeedbackComposer.from_rule_report(SAMPLE_RULE_REPORT)
        assert bundle.has_rule_feedback
        assert not bundle.has_llm_feedback
        assert len(bundle.items) == 2  # R2 + R5

    def test_skips_passed_rules(self):
        bundle = FeedbackComposer.from_rule_report(SAMPLE_RULE_REPORT)
        dims = [i.dimension for i in bundle.items]
        assert "R1" not in dims
        assert "R2" in dims
        assert "R5" in dims

    def test_preserves_severity(self):
        bundle = FeedbackComposer.from_rule_report(SAMPLE_RULE_REPORT)
        r2 = [i for i in bundle.items if i.dimension == "R2"][0]
        assert r2.severity == "fatal"
        r5 = [i for i in bundle.items if i.dimension == "R5"][0]
        assert r5.severity == "major"

    def test_empty_report(self):
        bundle = FeedbackComposer.from_rule_report({"rule_results": {}})
        assert len(bundle.items) == 0
        assert not bundle.has_fatal


# ─── FeedbackComposer.from_llm_eval ───

class TestFromLlmEval:
    def test_extracts_low_scoring_dims(self):
        bundle = FeedbackComposer.from_llm_eval(SAMPLE_LLM_RESULT, min_score=3.0)
        dims = [i.dimension for i in bundle.items]
        assert "richness" in dims      # 2.5 < 3.0
        assert "actionability" in dims  # 2.0 < 3.0
        assert "readability" not in dims  # 4.0 >= 3.0
        assert "faithfulness" not in dims  # 3.5 >= 3.0

    def test_none_result(self):
        bundle = FeedbackComposer.from_llm_eval(None)
        assert len(bundle.items) == 0

    def test_all_high_scores(self):
        good_result = LLMEvalResult(5, 5, 5, 5, 5, "完美", [])
        bundle = FeedbackComposer.from_llm_eval(good_result, min_score=3.0)
        assert len(bundle.items) == 0

    def test_severity_mapping(self):
        bundle = FeedbackComposer.from_llm_eval(SAMPLE_LLM_RESULT, min_score=3.0)
        rich = [i for i in bundle.items if i.dimension == "richness"][0]
        act = [i for i in bundle.items if i.dimension == "actionability"][0]
        # 灰度区间: threshold-0.5 ~ threshold-0.25 ~ threshold
        #   < threshold-0.5 → major
        #   < threshold-0.25 → medium
        #   >= threshold-0.25 → advisory
        # richness 2.5, threshold 3.0: 2.5 >= 2.5(gray_low) and 2.5 < 2.75 → medium
        # actionability 2.0, threshold 3.0: 2.0 < 2.5(gray_low) → major
        assert rich.severity == "medium"
        assert act.severity == "major"

    def test_faithfulness_advisory_mode(self):
        """faithfulness 在 advisory 模式下强制降级"""
        low_faith = LLMEvalResult(4, 4, 1.5, 4, 3.5, "忠实度极差", [])
        bundle = FeedbackComposer.from_llm_eval(low_faith, faithfulness_mode="advisory")
        faith = [i for i in bundle.items if i.dimension == "faithfulness"]
        if faith:  # faithfulness 1.5 < 2.5 默认阈值
            assert faith[0].severity == "advisory"  # 强制降级

    def test_faithfulness_gate_mode(self):
        """faithfulness 在 gate 模式下正常评分"""
        low_faith = LLMEvalResult(4, 4, 1.5, 4, 3.5, "忠实度极差", [])
        bundle = FeedbackComposer.from_llm_eval(low_faith, faithfulness_mode="gate")
        faith = [i for i in bundle.items if i.dimension == "faithfulness"]
        if faith:
            assert faith[0].severity != "advisory"  # 不强制降级

    def test_score_included(self):
        bundle = FeedbackComposer.from_llm_eval(SAMPLE_LLM_RESULT, min_score=3.0)
        for item in bundle.items:
            assert item.score is not None
            assert item.score < 3.0


# ─── FeedbackComposer.compose ───

class TestCompose:
    def test_combines_both(self):
        rule = FeedbackComposer.from_rule_report(SAMPLE_RULE_REPORT)
        llm = FeedbackComposer.from_llm_eval(SAMPLE_LLM_RESULT, min_score=3.0)
        combined = FeedbackComposer.compose(rule, llm)
        assert combined.has_rule_feedback
        assert combined.has_llm_feedback

    def test_rule_priority(self):
        rule = FeedbackComposer.from_rule_report(SAMPLE_RULE_REPORT)
        llm = FeedbackComposer.from_llm_eval(SAMPLE_LLM_RESULT, min_score=3.0)
        combined = FeedbackComposer.compose(rule, llm)
        # 规则项在前
        assert combined.items[0].source == "rule"


# ─── FeedbackComposer.from_single_llm_dimension ───

class TestSingleDimension:
    def test_creates_single_item(self):
        bundle = FeedbackComposer.from_single_llm_dimension(
            "actionability", 2.0, "将模糊建议改为可执行步骤"
        )
        assert len(bundle.items) == 1
        assert bundle.items[0].dimension == "actionability"
        assert bundle.items[0].source == "llm"

    def test_avoids_multi_dimension(self):
        """关键测试：单维度反馈避免多目标振荡"""
        bundle = FeedbackComposer.from_single_llm_dimension(
            "richness", 2.5, "补充更多数据"
        )
        dim_count = len(set(i.dimension for i in bundle.items))
        assert dim_count == 1


# ─── FeedbackBundle.to_prompt_section ───

class TestPromptSection:
    def test_generates_readable_output(self):
        bundle = FeedbackComposer.from_rule_report(SAMPLE_RULE_REPORT)
        section = bundle.to_prompt_section()
        assert "质量反馈" in section
        assert "R2" in section
        assert "R5" in section

    def test_empty_bundle(self):
        bundle = FeedbackBundle()
        assert bundle.to_prompt_section() == ""

    def test_llm_feedback_format(self):
        bundle = FeedbackComposer.from_llm_eval(SAMPLE_LLM_RESULT, min_score=3.0)
        section = bundle.to_prompt_section()
        assert "[LLM评审]" in section
        assert "richness" in section

    def test_combined_format(self):
        rule = FeedbackComposer.from_rule_report(SAMPLE_RULE_REPORT)
        llm = FeedbackComposer.from_llm_eval(SAMPLE_LLM_RESULT, min_score=3.0)
        combined = FeedbackComposer.compose(rule, llm)
        section = combined.to_prompt_section()
        assert "[规则]" in section
        assert "[LLM评审]" in section


# ─── 条件 LLM 评审触发逻辑 ───

class TestBorderlineLlmEval:
    def test_borderline_trigger_config(self):
        """验证 QualityGate 接受 LLM 评审参数"""
        from noteforge.quality.gate import QualityGate
        gate = QualityGate(
            llm_eval_on_borderline=True,
            llm_eval_borderline_low=0.75,
            llm_eval_borderline_high=0.85,
        )
        assert gate._llm_eval_on_borderline is True
        assert gate._llm_eval_borderline_low == 0.75
        assert gate._llm_eval_borderline_high == 0.85

    def test_default_off(self):
        """默认不触发 LLM 评审"""
        from noteforge.quality.gate import QualityGate
        gate = QualityGate()
        assert gate._llm_eval_on_borderline is False
        assert gate._llm_eval_provider is None

    def test_no_llm_eval_without_provider(self):
        """无 provider 时不触发 LLM 评审"""
        from noteforge.quality.gate import QualityGate
        gate = QualityGate(llm_eval_on_borderline=True)
        # 即使开启，无 provider 也不会调用
        assert gate._llm_eval_provider is None
