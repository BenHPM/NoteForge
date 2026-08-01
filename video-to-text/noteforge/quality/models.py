# -*- coding: utf-8 -*-
"""
NoteForge 质量评估数据模型

提取自 quality/gate.py 的 4 个 dataclass：
Issue、RuleResult、LLMEvalResult、QualityReport
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Union
from enum import Enum


class QualityGateFailure(Exception):
    """Raised when quality gate fails after max retries"""


class FailureClass(Enum):
    """EvalFailure 分类枚举 — 用于路由重试/降级/终止策略

    调用方根据 failure_class 决定行为，无需字符串匹配：
    - RETRYABLE: 瞬态错误（JSON 解析失败、网络超时），值得重试
    - TERMINAL: 永久性错误（API key 无效、模型不支持），重试无意义
    - DEGRADED: 部分成功（内容被过滤但仍有输出），可降级使用
    """
    RETRYABLE = "retryable"      # 瞬态错误，值得重试
    TERMINAL = "terminal"        # 永久性错误，重试无意义
    DEGRADED = "degraded"        # 部分成功，可降级使用


# reason → FailureClass 默认映射（新增 reason 时在此注册）
_REASON_CLASS_MAP = {
    "json_parse": FailureClass.RETRYABLE,
    "content_filter": FailureClass.DEGRADED,
    "empty": FailureClass.RETRYABLE,
    "timeout": FailureClass.RETRYABLE,
    "api_key": FailureClass.TERMINAL,
    "model_error": FailureClass.TERMINAL,
    "other": FailureClass.RETRYABLE,
}


@dataclass
class EvalFailure:
    """LLM 评估/解析失败的结构化错误（替代静默 None 返回）

    用于 gate.py llm_evaluate() 和 llm_providers.py _parse_200() 等场景，
    确保失败原因可追溯、可调试、可测试、可路由。

    调用方通过 failure_class 枚举路由行为，无需字符串匹配 reason：
        if ef.failure_class == FailureClass.RETRYABLE:
            retry()
        elif ef.failure_class == FailureClass.TERMINAL:
            abort()
        elif ef.failure_class == FailureClass.DEGRADED:
            use_with_warning()
    """
    reason: str                    # 失败原因分类（json_parse / content_filter / empty / timeout / other）
    raw_response: str              # 原始 LLM 返回文本（截断到 500 字，用于调试）
    retry_eligible: bool = True    # 是否值得重试（向后兼容，由 failure_class 推导）
    provider: str = ""             # 来源提供商（claude / openai / local）
    failure_class: FailureClass = FailureClass.RETRYABLE  # 分类枚举（路由用）

    def __post_init__(self):
        """根据 reason 自动推导 failure_class（如果未显式指定）"""
        # 如果 failure_class 仍是默认值 RETRYABLE，且 reason 有映射，则自动推导
        if self.failure_class == FailureClass.RETRYABLE and self.reason in _REASON_CLASS_MAP:
            self.failure_class = _REASON_CLASS_MAP[self.reason]
        # 同步 retry_eligible（向后兼容）
        if self.failure_class == FailureClass.TERMINAL:
            self.retry_eligible = False
        elif self.failure_class == FailureClass.DEGRADED:
            self.retry_eligible = False  # 降级输出不重试，直接使用

    def to_dict(self):
        return {
            "reason": self.reason,
            "raw_response": self.raw_response[:500],
            "retry_eligible": self.retry_eligible,
            "provider": self.provider,
            "failure_class": self.failure_class.value,
        }


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
    llm_eval: Optional[Union[LLMEvalResult, EvalFailure]] = None
    # 结构化错误记录（P0: 替代静默 None 传播）
    eval_failure: Optional[EvalFailure] = None

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
            if isinstance(self.llm_eval, EvalFailure):
                result["llm_eval"] = {"_type": "failure", **self.llm_eval.to_dict()}
            else:
                result["llm_eval"] = self.llm_eval.to_dict()
        if self.eval_failure:
            result["eval_failure"] = self.eval_failure.to_dict()
        return result


from noteforge.quality.heuristics import QualityMetrics  # noqa: F401 — 解除前向引用
