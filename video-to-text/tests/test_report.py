# -*- coding: utf-8 -*-
"""测试 noteforge.quality.report.generate_markdown_report"""

import os
import pytest
from noteforge.quality.models import Issue, RuleResult, QualityReport, LLMEvalResult
from noteforge.quality.heuristics import QualityMetrics
from noteforge.quality.report import generate_markdown_report


def _make_report(**overrides):
    """构建 QualityReport 的辅助函数"""
    defaults = dict(
        note_path="notes/test.md",
        source_path="transcripts/test.txt",
        total_score=0.85,
        overall_passed=True,
        summary="测试摘要",
        rule_results={},
        metrics=None,
        llm_eval=None,
    )
    defaults.update(overrides)
    return QualityReport(**defaults)


def _make_issue(rule_id="R1", rule_name="禁止虚构数据", severity="fatal",
                line_range="L10-12", description="虚构了50%的数据",
                suggestion="删除虚构数据"):
    return Issue(rule_id=rule_id, rule_name=rule_name, severity=severity,
                 line_range=line_range, description=description,
                 suggestion=suggestion)


def _make_rule_result(rule_id="R1", rule_name="禁止虚构数据",
                      score=0.5, passed=False, issues=None):
    return RuleResult(rule_id=rule_id, rule_name=rule_name,
                      score=score, passed=passed,
                      issues=issues or [])


def _make_metrics(**overrides):
    defaults = dict(
        compression_ratio=0.20,
        structure_score=0.85,
        info_density=0.90,
        readability_score=0.78,
        quote_ratio=0.12,
        action_specificity=0.60,
        overall_richness=0.75,
    )
    defaults.update(overrides)
    return QualityMetrics(**defaults)


class TestGenerateMarkdownReport:
    """generate_markdown_report 函数测试"""

    def test_full_report_with_issues_and_metrics(self):
        """完整报告（有 issues + metrics）→ 包含 逐项评分、问题清单、内容质量指标"""
        issue = _make_issue()
        rr = _make_rule_result(issues=[issue])
        metrics = _make_metrics()
        report = _make_report(
            rule_results={"R1": rr},
            metrics=metrics,
        )
        md = generate_markdown_report(report)

        assert "逐项评分" in md
        assert "问题清单" in md
        assert "内容质量指标" in md
        # 验证 issue 内容出现
        assert "FATAL" in md
        assert "虚构了50%的数据" in md
        # 验证 metrics 指标出现
        assert "压缩比" in md
        assert "结构丰富度" in md
        assert "综合丰富度" in md

    def test_report_no_issues(self):
        """无 issue 报告 → 包含 无问题发现"""
        rr = _make_rule_result(score=1.0, passed=True, issues=[])
        report = _make_report(rule_results={"R1": rr})
        md = generate_markdown_report(report)

        assert "无问题发现" in md
        assert "问题清单" not in md

    def test_report_with_llm_eval(self):
        """有 LLM 评审 → 包含 LLM 深度评审"""
        llm = LLMEvalResult(
            richness_score=4.0,
            readability_score=3.5,
            faithfulness_score=4.5,
            actionability_score=3.0,
            overall_score=3.8,
            feedback="整体质量不错，但行动项可以更具体",
            suggestions=["增加具体行动指引", "补充数据来源标注"],
        )
        report = _make_report(llm_eval=llm)
        md = generate_markdown_report(report)

        assert "LLM 深度评审" in md
        assert "3.8/5" in md
        assert "整体质量不错" in md
        assert "增加具体行动指引" in md

    def test_report_no_metrics(self):
        """无 metrics → 不包含 内容质量指标 段"""
        report = _make_report(metrics=None)
        md = generate_markdown_report(report)

        assert "内容质量指标" not in md

    def test_report_pass_fail_status(self):
        """报告显示通过/未通过状态"""
        report_pass = _make_report(overall_passed=True, total_score=0.95)
        md_pass = generate_markdown_report(report_pass)
        assert "✅ 通过" in md_pass

        report_fail = _make_report(overall_passed=False, total_score=0.25)
        md_fail = generate_markdown_report(report_fail)
        assert "❌ 未通过" in md_fail

    def test_quality_metrics_to_dict(self):
        """QualityMetrics.to_dict() 正确性"""
        metrics = _make_metrics()
        d = metrics.to_dict()

        assert d["compression_ratio"] == 0.200
        assert d["structure_score"] == 0.85
        assert d["info_density"] == 0.90
        assert d["readability_score"] == 0.78
        assert d["quote_ratio"] == 0.120
        assert d["action_specificity"] == 0.60
        assert d["overall_richness"] == 0.75
        # 确认 round 生效
        assert isinstance(d["compression_ratio"], float)
