# -*- coding: utf-8 -*-
"""
NoteForge 质量评估数据模型

提取自 quality/gate.py 的 4 个 dataclass：
Issue、RuleResult、LLMEvalResult、QualityReport
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional


class QualityGateFailure(Exception):
    """Raised when quality gate fails after max retries"""


@dataclass
class Issue:
    """单条质量问题"""
    rule_id: str
    rule_name: str
    severity: str          # fatal / major / medium
    line_range: str        # 笔记中问题出现的行范围
    description: str       # 问题描述
    suggestion: str        # 修正建议


@dataclass
class RuleResult:
    """单条规则的检查结果"""
    rule_id: str
    rule_name: str
    score: float           # 0.0 ~ 1.0
    passed: bool
    issues: List[Issue] = field(default_factory=list)


@dataclass
class LLMEvalResult:
    """LLM 评审结果（需要 API 调用）"""
    richness_score: float       # 内容丰富度（1-5）
    readability_score: float    # 可读性（1-5）
    faithfulness_score: float   # 忠实度（1-5）
    actionability_score: float  # 可行动性（1-5）
    overall_score: float        # 综合评分（1-5）
    feedback: str               # LLM 给出的具体反馈
    suggestions: List[str]      # 改进建议

    def to_dict(self):
        return {
            "richness_score": round(self.richness_score, 1),
            "readability_score": round(self.readability_score, 1),
            "faithfulness_score": round(self.faithfulness_score, 1),
            "actionability_score": round(self.actionability_score, 1),
            "overall_score": round(self.overall_score, 1),
            "feedback": self.feedback,
            "suggestions": self.suggestions,
        }


@dataclass
class QualityReport:
    """完整质量评估报告"""
    # 核心：笔记和原文的文本内容（规则引擎必需）
    note_text: str = ""
    source_text: str = ""
    # 可读标签（用于报告输出展示，不参与逻辑）
    note_label: str = "<note>"
    source_label: str = "<source>"
    # 兼容：旧版文件路径（可选，仅在需要文件级引用时使用）
    note_path: Optional[str] = None
    source_path: Optional[str] = None
    # 评估结果
    total_score: float = 0.0
    rule_results: Dict[str, RuleResult] = field(default_factory=dict)
    overall_passed: bool = False
    summary: str = ""
    # 可选扩展
    metrics: Optional['QualityMetrics'] = None
    llm_eval: Optional[LLMEvalResult] = None

    def to_dict(self):
        result = {
            "note_label": self.note_label,
            "source_label": self.source_label,
            "total_score": round(self.total_score, 2),
            "overall_passed": self.overall_passed,
            "rule_results": {
                rid: {
                    "score": round(rr.score, 2),
                    "passed": rr.passed,
                    "issue_count": len(rr.issues),
                    "issues": [
                        {
                            "severity": iss.severity,
                            "line_range": iss.line_range,
                            "description": iss.description,
                            "suggestion": iss.suggestion
                        }
                        for iss in rr.issues
                    ]
                }
                for rid, rr in self.rule_results.items()
            },
            "summary": self.summary
        }
        if self.note_path:
            result["note_path"] = self.note_path
        if self.source_path:
            result["source_path"] = self.source_path
        if self.metrics:
            result["metrics"] = self.metrics.to_dict()
        if self.llm_eval:
            result["llm_eval"] = self.llm_eval.to_dict()
        return result


from noteforge.quality.heuristics import QualityMetrics  # noqa: F401 — 解除前向引用
