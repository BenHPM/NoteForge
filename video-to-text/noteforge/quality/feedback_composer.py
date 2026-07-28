# -*- coding: utf-8 -*-
"""
FeedbackComposer — 将质量反馈（规则 + LLM 维度）组合为结构化重试 prompt

设计原则（基于 ForgeCouncil 多专家讨论共识）：
1. LLM 评审反馈不触发自动重试，仅作为人工审查或条件触发的辅助信息
2. 反馈必须是单维度的、结构化的（不是原始分数），避免多目标振荡
3. 规则反馈和 LLM 维度反馈解耦，PromptBuilder 不直接处理 LLM 维度
4. 每条反馈必须包含：维度名、具体问题描述、修正建议

用法:
    from noteforge.quality.feedback_composer import FeedbackComposer

    # 从规则报告构建（现有路径）
    feedback = FeedbackComposer.from_rule_report(quality_report_dict)

    # 从 LLM 评审结果构建（新路径）
    feedback = FeedbackComposer.from_llm_eval(llm_eval_result, min_score=3.0)

    # 组合两者
    combined = FeedbackComposer.compose(rule_feedback, llm_feedback)

    # 生成 prompt 片段
    prompt_section = combined.to_prompt_section()
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class FeedbackItem:
    """单条结构化反馈"""
    source: str           # "rule" 或 "llm"
    dimension: str        # 规则 ID (如 "R1") 或 LLM 维度 (如 "readability")
    severity: str         # fatal / major / medium / advisory
    description: str      # 具体问题描述
    suggestion: str       # 修正建议
    score: Optional[float] = None  # 原始分数（可选，LLM 维度用）


@dataclass
class FeedbackBundle:
    """一组结构化反馈"""
    items: List[FeedbackItem] = field(default_factory=list)

    @property
    def has_fatal(self) -> bool:
        return any(i.severity == "fatal" for i in self.items)

    @property
    def has_llm_feedback(self) -> bool:
        return any(i.source == "llm" for i in self.items)

    @property
    def has_rule_feedback(self) -> bool:
        return any(i.source == "rule" for i in self.items)

    def to_prompt_section(self) -> str:
        """生成可嵌入重试 prompt 的反馈段落"""
        if not self.items:
            return ""

        lines = ["## 质量反馈\n"]

        # 按严重度排序
        severity_order = {"fatal": 0, "major": 1, "medium": 2, "advisory": 3}
        sorted_items = sorted(self.items, key=lambda i: severity_order.get(i.severity, 99))

        for idx, item in enumerate(sorted_items, 1):
            severity_icon = {
                "fatal": "🔴", "major": "🟠", "medium": "🟡", "advisory": "🔵"
            }.get(item.severity, "⚪")

            source_tag = "[规则]" if item.source == "rule" else "[LLM评审]"

            lines.append(
                f"{idx}. {severity_icon} {source_tag} **{item.dimension}** "
                f"({item.severity})"
            )
            lines.append(f"   - 问题: {item.description}")
            lines.append(f"   - 建议: {item.suggestion}")
            if item.score is not None:
                lines.append(f"   - 评分: {item.score:.1f}")
            lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> Dict:
        """序列化为字典"""
        return {
            "items": [
                {
                    "source": i.source,
                    "dimension": i.dimension,
                    "severity": i.severity,
                    "description": i.description,
                    "suggestion": i.suggestion,
                    "score": i.score,
                }
                for i in self.items
            ]
        }


class FeedbackComposer:
    """质量反馈组合器 — 规则反馈 + LLM 维度反馈的统一接口"""

    @staticmethod
    def from_rule_report(quality_report: Dict) -> FeedbackBundle:
        """
        从规则质量报告构建反馈

        Args:
            quality_report: QualityManager.run_quality_gate_on_text() 返回的字典

        Returns:
            FeedbackBundle（仅包含规则反馈）
        """
        items = []

        rule_results = quality_report.get("rule_results", {})
        for rid in sorted(rule_results.keys()):
            rr = rule_results[rid]
            if not rr.get("passed", True):
                for issue in rr.get("issues", []):
                    items.append(FeedbackItem(
                        source="rule",
                        dimension=rid,
                        severity=issue.get("severity", "medium"),
                        description=issue.get("description", ""),
                        suggestion=issue.get("suggestion", ""),
                    ))

        return FeedbackBundle(items=items)

    @staticmethod
    def from_llm_eval(llm_eval_result, min_score: float = 3.0,
                      dimension_thresholds: dict = None,
                      faithfulness_mode: str = "advisory") -> FeedbackBundle:
        """
        从 LLM 评审结果构建反馈（仅包含低于阈值的维度）

        Args:
            llm_eval_result: LLMEvalResult 实例
            min_score: 默认维度评分阈值（低于此值才生成反馈）
            dimension_thresholds: 维度特定阈值覆盖，如 {"faithfulness": 2.5}
            faithfulness_mode: faithfulness 维度处理模式
                - "advisory": 仅记录，不影响 pass/fail（推荐，因同模型自评循环风险）
                - "gate": 参与 pass/fail 决策（需人工校准后启用）

        Returns:
            FeedbackBundle（仅包含 LLM 维度反馈）
        """
        items = []

        if llm_eval_result is None:
            return FeedbackBundle(items=items)

        # 维度 → (分数字段, 描述, 修正建议模板, 默认阈值)
        dimensions = [
            ("richness", "richness_score", "内容丰富度不足",
             "补充遗漏的重要议题、具体数据和概念框架", 3.0),
            ("readability", "readability_score", "可读性不足",
             "优化段落长度、增加标题分层、使用列表和表格", 3.0),
            ("faithfulness", "faithfulness_score", "忠实度不足",
             "对照原文逐句核实数字、人名和因果论断", 2.5),  # 偏低阈值：同模型自评均值2.36
            ("actionability", "actionability_score", "可行动性不足",
             "将模糊建议改为含时间/工具/步骤的具体行动项", 3.0),
        ]

        for dim_name, score_attr, desc_template, fix_template, default_threshold in dimensions:
            # 维度特定阈值覆盖
            threshold = (dimension_thresholds or {}).get(dim_name, default_threshold)
            score = getattr(llm_eval_result, score_attr, None)
            if score is not None and score < threshold:
                # 确定严重度（含灰度区间逻辑）
                # 灰度区间：分数在阈值-0.5到阈值之间为 advisory（不硬判）
                gray_zone_low = threshold - 0.5
                if score < gray_zone_low:
                    severity = "major"
                elif score < (threshold - 0.25):
                    severity = "medium"
                else:
                    severity = "advisory"

                # faithfulness 特殊处理：advisory 模式下强制降级
                if dim_name == "faithfulness" and faithfulness_mode == "advisory":
                    severity = "advisory"

                # 提取 LLM 的建议（如果有）
                suggestions = llm_eval_result.suggestions or []
                specific_fix = fix_template
                if suggestions:
                    # 找到与该维度相关的建议
                    for s in suggestions:
                        if any(kw in s for kw in ["丰富", "深度", "遗漏"]):
                            if dim_name == "richness":
                                specific_fix = s
                                break
                        elif any(kw in s for kw in ["可读", "结构", "段落"]):
                            if dim_name == "readability":
                                specific_fix = s
                                break
                        elif any(kw in s for kw in ["忠实", "原文", "编造"]):
                            if dim_name == "faithfulness":
                                specific_fix = s
                                break
                        elif any(kw in s for kw in ["行动", "具体", "执行"]):
                            if dim_name == "actionability":
                                specific_fix = s
                                break

                items.append(FeedbackItem(
                    source="llm",
                    dimension=dim_name,
                    severity=severity,
                    description=f"{desc_template}（评分 {score:.1f}/5）",
                    suggestion=specific_fix,
                    score=score,
                ))

        return FeedbackBundle(items=items)

    @staticmethod
    def compose(rule_feedback: FeedbackBundle,
                llm_feedback: FeedbackBundle) -> FeedbackBundle:
        """
        组合规则反馈和 LLM 维度反馈

        优先级：规则 fatal > 规则 major > LLM major > 规则 medium > LLM advisory
        同一问题不重复（按 dimension 去重，规则优先）

        Args:
            rule_feedback: 规则反馈
            llm_feedback: LLM 维度反馈

        Returns:
            FeedbackBundle（合并后的反馈）
        """
        # 规则反馈维度集合（用于去重）
        rule_dims = {i.dimension for i in rule_feedback.items}

        # LLM 反馈中去除与规则重叠的维度
        # 注意: 规则维度是 R1-R12，LLM 维度是 richness 等，通常不重叠
        # 但如果未来规则扩展覆盖了 LLM 维度，这里会去重
        filtered_llm = [
            i for i in llm_feedback.items
            if i.dimension not in rule_dims
        ]

        all_items = rule_feedback.items + filtered_llm

        return FeedbackBundle(items=all_items)

    @staticmethod
    def from_single_llm_dimension(dimension: str, score: float,
                                   suggestion: str) -> FeedbackBundle:
        """
        构建单维度 LLM 反馈（用于条件触发 — 只反馈一个维度）

        这是 ForgeCouncil 共识推荐的方式：重试循环中只反馈一个 LLM 维度，
        避免多目标优化导致振荡。

        Args:
            dimension: 维度名（richness/readability/faithfulness/actionability）
            score: 评分（1-5）
            suggestion: 修正建议

        Returns:
            FeedbackBundle（仅包含一条 LLM 反馈）
        """
        if score < 2.0:
            severity = "major"
        elif score < 3.0:
            severity = "medium"
        else:
            severity = "advisory"

        dim_descriptions = {
            "richness": "内容丰富度不足",
            "readability": "可读性不足",
            "faithfulness": "忠实度不足",
            "actionability": "可行动性不足",
        }

        return FeedbackBundle(items=[FeedbackItem(
            source="llm",
            dimension=dimension,
            severity=severity,
            description=f"{dim_descriptions.get(dimension, dimension)}（评分 {score:.1f}/5）",
            suggestion=suggestion,
            score=score,
        )])
