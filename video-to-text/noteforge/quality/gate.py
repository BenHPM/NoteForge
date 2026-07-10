# -*- coding: utf-8 -*-
"""
NoteForge 笔记质量评分引擎

QualityGate 类：R0-R12 规则权重 + 评估入口 + LLM 评审。
规则检查函数委托给 noteforge.quality.rules 模块。

入口：
  evaluate(paths)       — 文件路径入口（向后兼容）
  evaluate_text(texts)  — 纯文本入口（推荐，规则迭代用）
  evaluate_rule(id, texts) — 单条规则入口（调试用）
"""

import os
import re
import json
import logging
from typing import Optional, List

from noteforge.infra.file_io import read_file
from noteforge.quality.models import Issue, RuleResult, LLMEvalResult, QualityReport
from noteforge.quality.heuristics import QualityMetrics, compute_metrics
from noteforge.quality import rules

logger = logging.getLogger('noteforge.quality')


class QualityGate:
    """笔记质量评分引擎"""

    # 规则权重配置
    RULE_WEIGHTS = {
        "R0": 0,    # 内容完整性（硬校验，权重 0 = 不参与加权平均）
        "R1": 20,   # 禁止虚构数据
        "R2": 15,   # 禁止越界增补
        "R3": 20,   # 禁止事实反转
        "R4": 10,   # 禁止概念失真
        "R5": 10,   # 覆盖度底线
        "R6": 5,    # 术语一致性
        "R7": 10,   # 框架完整性
        "R8": 5,    # 洞察可行动性
        "R9": 5,    # 分层准确性
        "R10": 5,   # 时间线准确性
        "R11": 5,   # 引用归属
        "R12": 5,   # 人名/数字一致性
    }

    # 通用数字/百分比易被编造的模式（跨领域适用）
    FABRICATED_PATTERNS = [
        # 百分比类
        r'占比\s*[约达]?\s*[\d.]+%',
        r'权重\s*[约达]?\s*[\d.]+%',
        r'贡献\s*[约达]?\s*[\d.]+%',
        r'[约近超]?\s*[\d.]+%\s*[以之]*[外来]',
        r'占比.*?(\d+[./]\d+)',
        # 倍数/比例类
        r'[约近]\s*[\d.]+倍',
        r'增长\s*[约达]?\s*[\d.]+%',
        r'提升\s*[约达]?\s*[\d.]+%',
        r'下降\s*[约达]?\s*[\d.]+%',
        r'减少\s*[约达]?\s*[\d.]+%',
        # 精确量化类（原文只有定性描述时）
        r'第\s*[一二三四五六七八九十\d]+\s*[名位]',
        r'排名\s*[第前]\s*\d+',
        r'[总均平]?\s*(?:达到?|超过?|突破)\s*[\d,.]+\s*[万亿百千]',
        # 分数/比例类
        r'[\d.]+\s*[：:]\s*[\d.]+',  # X:Y 比例
    ]

    # 需保留关键限定词的专业概念（概念名 -> 必须包含的关键词列表）
    # 可通过 YAML 配置扩展，此处为内置默认值
    DEFAULT_KEY_CONCEPTS = {
        "T0策略": ["中低频", "长周期预测", "高抛低吸", "自动化"],
        "指增策略": ["超额收益", "宽基指数", "成分股"],
        "周期投资": ["供给端", "资本开支", "ROE", "基本面趋势"],
        "非可解释性因子": ["不可解释", "非线性组合", "模型训练"],
        "高频策略": ["毫秒级", "盘口", "竞争"],
        "价值投资": ["内在价值", "长期持有", "定价"],
    }

    # 内容类型到领域的映射（用于 R4 KEY_CONCEPTS 加载）
    # 加载全部领域：R4 仅在概念确实出现在笔记中时触发，额外概念无害
    # 这避免了 lecture 关于 geopolitics 时漏检 geopolitics 概念的问题
    CONTENT_TYPE_DOMAINS = {
        'lecture': ['finance', 'short_video', 'geopolitics'],
        'tutorial': ['finance', 'short_video', 'geopolitics'],
        'interview': ['finance', 'short_video', 'geopolitics'],
        'podcast': ['finance', 'short_video', 'geopolitics'],
        'meeting': [],
    }

    def __init__(self, rules_path: Optional[str] = None,
                 content_type: Optional[str] = None,
                 fatal_rules_must_pass: bool = True):
        """
        Args:
            rules_path: note_generation_rules.yaml 路径（可选，用于加载 KEY_CONCEPTS 配置）
            content_type: 内容类型（决定加载哪些领域的概念）
            fatal_rules_must_pass: 致命规则是否必须全部通过（可配置关闭）
        """
        self._key_concepts = dict(self.DEFAULT_KEY_CONCEPTS)
        self._content_type = content_type
        self._fatal_rules_must_pass = fatal_rules_must_pass
        # R5 覆盖度阈值（默认值与 rules_coverage.py 一致）
        self._r5_fatal_threshold = 0.30
        self._r5_major_threshold = 0.80
        if rules_path and os.path.exists(rules_path):
            try:
                from noteforge.config import load_yaml
                config = load_yaml(rules_path)
                # 加载 R5 阈值配置
                rules_config = config.get('rules', {})
                r5_rule = rules_config.get('R5_覆盖度底线', {})
                r5_thresholds = r5_rule.get('thresholds', {})
                if r5_thresholds:
                    self._r5_fatal_threshold = float(r5_thresholds.get('fatal', 0.30))
                    self._r5_major_threshold = float(r5_thresholds.get('major', 0.80))
                # 加载 KEY_CONCEPTS 配置
                yaml_concepts = config.get('key_concepts', {})
                if yaml_concepts:
                    # 加载通用概念（_general）
                    general = yaml_concepts.get('_general', {})
                    if general:
                        self._key_concepts.update(general)
                    # 根据 content_type 加载对应领域的概念
                    domains = self.CONTENT_TYPE_DOMAINS.get(
                        content_type or '', ['finance']
                    )
                    for domain in domains:
                        domain_concepts = yaml_concepts.get(domain, {})
                        if domain_concepts:
                            self._key_concepts.update(domain_concepts)
            except Exception as e:
                logger.debug(f"概念加载失败，回退到内置默认: {e}")

    # ----------------------------------------------------------
    # 纯文本入口（推荐：规则迭代、调试、嵌入其他系统）
    # ----------------------------------------------------------
    def evaluate_text(self, note_text: str, source_text: str,
                      note_label: str = "<note>",
                      source_label: str = "<source>") -> QualityReport:
        """
        纯文本质量评估：给定笔记和原文文本，返回完整质量报告。

        不涉及任何文件 IO，不依赖 PipelineContext，不依赖路径。
        这是规则迭代的首选入口。

        Args:
            note_text: 笔记正文（Markdown）
            source_text: 转写原文
            note_label: 可读标签（报告输出用，如 "ep01.md"）
            source_label: 可读标签（报告输出用，如 "ep01.txt"）

        Returns:
            QualityReport（包含 note_text 和 source_text，自包含上下文）
        """
        # R0: 硬校验 — 笔记正文长度（排除标题、元数据、分隔线）
        body_lines = [
            line for line in note_text.split('\n')
            if line.strip()
            and not line.startswith('#')
            and not line.startswith('>')
            and not line.startswith('---')
            and not line.startswith('*笔记整理')
            and not line.startswith('*学习来源')
            and '课程定位' not in line
            and '待补充' not in line
        ]
        body_text = '\n'.join(body_lines).strip()

        if len(body_text) < 200:
            return QualityReport(
                note_text=note_text, source_text=source_text,
                note_label=note_label, source_label=source_label,
                total_score=0.0,
                rule_results={
                    "R0": RuleResult(
                        "R0", "内容完整性",
                        0.0, False,
                        [Issue(
                            rule_id="R0",
                            rule_name="内容完整性",
                            severity="fatal",
                            line_range="全文",
                            description=f"笔记正文仅 {len(body_text)} 字，低于 200 字下限。"
                                        f"可能原因: LLM 内容安全过滤、生成失败、或输出被截断",
                            suggestion="检查 LLM 返回是否有错误信息，尝试切换提供商重试",
                        )],
                    ),
                },
                overall_passed=False,
                summary=f"❌ 正文过短 ({len(body_text)} 字 < 200 字下限)，可能被内容安全过滤",
            )

        results: dict = {}
        results["R0"] = RuleResult("R0", "内容完整性", 1.0, True)
        results["R1"] = rules.check_fabricated_data(self.FABRICATED_PATTERNS, note_text, source_text)
        results["R2"] = rules.check_unmarked_additions(note_text, source_text)
        results["R3"] = rules.check_semantic_reversal(note_text, source_text)
        results["R4"] = rules.check_concept_distortion(self._key_concepts, note_text)
        results["R5"] = rules.check_coverage(note_text, source_text,
                                             self._r5_fatal_threshold,
                                             self._r5_major_threshold)
        results["R6"] = rules.check_consistency(note_text)
        results["R7"] = rules.check_framework_completeness(note_text)
        results["R8"] = rules.check_insight_actionability(note_text)
        results["R9"] = rules.check_layering_accuracy(note_text)
        results["R10"] = rules.check_timeline_accuracy(note_text, source_text)
        results["R11"] = rules.check_quote_attribution(note_text, source_text)
        results["R12"] = rules.check_name_number_consistency(note_text, source_text)

        # 加权总分
        total_weight = sum(self.RULE_WEIGHTS.values())
        total_score = sum(
            results[rid].score * self.RULE_WEIGHTS[rid]
            for rid in self.RULE_WEIGHTS
            if rid in results
        ) / total_weight

        # 致命规则校验
        fatal_passed = True
        if self._fatal_rules_must_pass:
            fatal_passed = all(
                results[rid].passed
                for rid in ["R1", "R2", "R3", "R5"]
                if rid in results
            )

        overall_passed = total_score >= 0.80 and fatal_passed

        # 汇总 issues
        all_issues = []
        for rid in self.RULE_WEIGHTS:
            if rid in results:
                all_issues.extend(results[rid].issues)
        fatal_count = sum(1 for i in all_issues if i.severity == "fatal")
        major_count = sum(1 for i in all_issues if i.severity == "major")
        medium_count = sum(1 for i in all_issues if i.severity == "medium")

        summary = (
            f"总分: {total_score:.2%} | "
            f"{'✅ 通过' if overall_passed else '❌ 未通过'} | "
            f"致命:{fatal_count} 严重:{major_count} 中等:{medium_count}"
        )

        # 启发式指标
        metrics = self._compute_metrics(note_text, source_text, body_text)

        return QualityReport(
            note_text=note_text, source_text=source_text,
            note_label=note_label, source_label=source_label,
            total_score=total_score,
            rule_results=results,
            overall_passed=overall_passed,
            summary=summary,
            metrics=metrics,
        )

    # ----------------------------------------------------------
    # 文件路径入口（向后兼容）
    # ----------------------------------------------------------
    def evaluate(self, note_path: str, source_path: str) -> QualityReport:
        """
        文件路径入口：读取两个文件后委托给 evaluate_text()。

        保留此方法以确保现有调用方兼容。
        新代码推荐使用 evaluate_text()。
        """
        note_text = read_file(note_path)
        source_text = read_file(source_path)
        note_label = os.path.basename(note_path)
        source_label = os.path.basename(source_path)
        report = self.evaluate_text(note_text, source_text,
                                    note_label=note_label,
                                    source_label=source_label)
        report.note_path = note_path
        report.source_path = source_path
        return report

    # ----------------------------------------------------------
    # 单条规则入口（调试用）
    # ----------------------------------------------------------
    @classmethod
    def evaluate_rule(cls, rule_id: str, note_text: str, source_text: str,
                      content_type: Optional[str] = None,
                      rules_path: Optional[str] = None,
                      **rule_kwargs) -> RuleResult:
        """
        单独运行一条规则，用于调试和迭代。

        Args:
            rule_id: 规则 ID（如 "R1", "R4", "R8"）
            note_text: 笔记文本
            source_text: 转写原文（部分规则不需要，传空字符串即可）
            content_type: 内容类型（影响 R4 加载哪些概念）
            rules_path: 规则配置路径（用于加载 KEY_CONCEPTS）
            **rule_kwargs: 规则特定参数

        Returns:
            RuleResult（单条规则的检查结果）

        Usage:
            result = QualityGate.evaluate_rule("R4", note, source, content_type="lecture")
            for issue in result.issues:
                print(f"[{issue.line_range}] {issue.description}")
        """
        gate = cls(rules_path=rules_path, content_type=content_type)

        # 规则 ID → 函数映射
        rule_functions = {
            "R1": lambda n, s: rules.check_fabricated_data(gate.FABRICATED_PATTERNS, n, s),
            "R2": lambda n, s: rules.check_unmarked_additions(n, s),
            "R3": lambda n, s: rules.check_semantic_reversal(n, s),
            "R4": lambda n, s: rules.check_concept_distortion(gate._key_concepts, n),
            "R5": lambda n, s: rules.check_coverage(
                n, s, gate._r5_fatal_threshold, gate._r5_major_threshold
            ),
            "R6": lambda n, s: rules.check_consistency(n),
            "R7": lambda n, s: rules.check_framework_completeness(n),
            "R8": lambda n, s: rules.check_insight_actionability(n),
            "R9": lambda n, s: rules.check_layering_accuracy(n),
            "R10": lambda n, s: rules.check_timeline_accuracy(n, s),
            "R11": lambda n, s: rules.check_quote_attribution(n, s),
            "R12": lambda n, s: rules.check_name_number_consistency(n, s),
        }

        rule_names = {
            "R1": "禁止虚构数据",
            "R2": "禁止越界增补",
            "R3": "禁止事实反转",
            "R4": "禁止概念失真",
            "R5": "覆盖度底线",
            "R6": "术语一致性",
            "R7": "框架完整性",
            "R8": "洞察可行动性",
            "R9": "分层准确性",
            "R10": "时间线准确性",
            "R11": "引用归属",
            "R12": "人名/数字一致性",
        }

        if rule_id not in rule_functions:
            raise ValueError(
                f"未知规则 ID: {rule_id}。"
                f"可选值: {sorted(rule_functions.keys())}"
            )

        fn = rule_functions[rule_id]
        result = fn(note_text, source_text)

        # 如果传入 rule_kwargs，注入到结果的额外字段（用于调试 trace）
        if rule_kwargs:
            result.issues = [
                Issue(
                    rule_id=iss.rule_id,
                    rule_name=iss.rule_name,
                    severity=iss.severity,
                    line_range=iss.line_range,
                    description=f"{iss.description} [调试: {rule_kwargs}]",
                    suggestion=iss.suggestion,
                )
                for iss in result.issues
            ]

        logger.debug(
            f"evaluate_rule({rule_id}) → score={result.score:.2f}, "
            f"passed={result.passed}, issues={len(result.issues)}"
        )
        return result

    # ----------------------------------------------------------
    # 启发式质量指标（零 API 成本）
    # ----------------------------------------------------------
    def _compute_metrics(self, note_text: str, source_text: str,
                         body_text: str) -> QualityMetrics:
        """计算启发式质量指标（委托给 heuristics 模块）"""
        return compute_metrics(note_text, source_text, body_text)

    # ----------------------------------------------------------
    # LLM 评审（需要 API 调用，可选）
    # ----------------------------------------------------------
    def llm_evaluate(self, note_text: str, source_text: str,
                     provider=None) -> Optional[LLMEvalResult]:
        """
        用 LLM 对笔记做多维度深度评审

        Args:
            note_text: 笔记文本
            source_text: 转写原文
            provider: LLMProvider 实例（为 None 时跳过）

        Returns:
            LLMEvalResult 或 None（无法调用时）
        """
        if provider is None:
            return None

        eval_prompt = """请对以下笔记做多维度质量评审。对照转写原文，给出 1-5 分评分和具体反馈。

## 评分维度（每项 1-5 分）
1. **内容丰富度** (richness): 信息量是否充足？是否遗漏重要议题？深度是否足够？
2. **可读性** (readability): 段落长度是否适中？结构是否清晰？是否易于快速浏览？
3. **忠实度** (faithfulness): 是否忠实于原文？有无编造或歪曲？原话是否被保留？
4. **可行动性** (actionability): 行动清单是否具体可执行？洞察是否可迁移？

## 输出格式（严格 JSON）
```json
{
  "richness": 4.0,
  "readability": 3.5,
  "faithfulness": 5.0,
  "actionability": 3.0,
  "overall": 3.9,
  "feedback": "一句话总评",
  "suggestions": ["建议1", "建议2"]
}
```

## 转写原文（前 3000 字）
{source}

## 笔记全文
{note}

请严格按 JSON 格式输出评分。"""

        # 截取原文前 3000 字（避免超 token）
        source_preview = source_text[:3000]
        full_prompt = eval_prompt.format(source=source_preview, note=note_text)

        try:
            result = provider.generate(
                system_prompt="你是一位严格的笔记质量评审专家。只输出 JSON。",
                user_prompt=full_prompt,
                max_tokens=500,
                temperature=0.1,
            )

            # 解析 JSON
            json_match = re.search(r'\{[^{}]*\}', result, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return LLMEvalResult(
                    richness_score=float(data.get('richness', 3)),
                    readability_score=float(data.get('readability', 3)),
                    faithfulness_score=float(data.get('faithfulness', 3)),
                    actionability_score=float(data.get('actionability', 3)),
                    overall_score=float(data.get('overall', 3)),
                    feedback=data.get('feedback', ''),
                    suggestions=data.get('suggestions', []),
                )
        except Exception as e:
            logger.warning(f"LLM 评审失败: {e}")

        return None
