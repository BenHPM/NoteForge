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
from typing import Optional, List, Union

from noteforge.infra.file_io import read_file
from noteforge.quality.models import (
    Issue, RuleResult, LLMEvalResult, EvalFailure, FailureClass, QualityReport
)
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
                 fatal_rules_must_pass: bool = True,
                 llm_eval_provider=None,
                 llm_eval_on_borderline: bool = False,
                 llm_eval_borderline_low: float = 0.75,
                 llm_eval_borderline_high: float = 0.85):
        """
        Args:
            rules_path: note_generation_rules.yaml 路径（可选，用于加载 KEY_CONCEPTS 配置）
            content_type: 内容类型（决定加载哪些领域的概念）
            fatal_rules_must_pass: 致命规则是否必须全部通过（可配置关闭）
            llm_eval_provider: LLMProvider 实例（可选，用于条件触发 LLM 评审）
            llm_eval_on_borderline: 是否在边界分数时触发 LLM 评审（默认关闭）
            llm_eval_borderline_low: 边界分数下限（默认 0.75）
            llm_eval_borderline_high: 边界分数上限（默认 0.85）
        """
        self._key_concepts = dict(self.DEFAULT_KEY_CONCEPTS)
        self._content_type = content_type
        self._fatal_rules_must_pass = fatal_rules_must_pass
        self._llm_eval_provider = llm_eval_provider
        self._llm_eval_on_borderline = llm_eval_on_borderline
        self._llm_eval_borderline_low = llm_eval_borderline_low
        self._llm_eval_borderline_high = llm_eval_borderline_high
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
        # R0: 硬校验 — 笔记正文长度（排除标题、元数据、分隔线、签名）
        _R0_SKIP = re.compile(
            r'^\s*'                          # 前导空白
            r'(?:#+\s*|>\s*|---\s*'          # 标题、引用、分隔线
            r'|\*(?:笔记整理|学习来源)'        # 签名行
            r'|\*\*(?:笔记整理|学习来源|课程定位|待补充)\*\*'  # 加粗签名
            r')',
            re.IGNORECASE
        )
        _R0_INLINE_SKIP = re.compile(
            r'课程定位|待补充',
        )
        body_lines = [
            line for line in note_text.split('\n')
            if line.strip()
            and not _R0_SKIP.match(line)
            and not _R0_INLINE_SKIP.search(line)
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
        results["R5"] = rules.check_coverage(note_text, source_text)
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
        # 只统计 fatal 严重度 issue：R1/R2 恒发 fatal；R3 高置信反转才 fatal
        # （medium 为"低置信疑似需人工确认"，不应硬拦）；R5 覆盖率 <30% 才 fatal
        # （30-80% 为 major，属可修复提示）。这与文档语义一致——R3 medium 和
        # R5 major 都只是报告项，不再把整篇忠实笔记拒之门外。
        fatal_passed = True
        if self._fatal_rules_must_pass:
            fatal_passed = all(
                not any(i.severity == "fatal" for i in results[rid].issues)
                for rid in ["R1", "R2", "R3", "R5"]
                if rid in results
            )

        overall_passed = total_score >= 0.80 and fatal_passed

        # 启发式指标护栏（独立于加权分数，作为硬性守卫）
        # 这些指标是确定性的、零 API 成本的，用于捕获规则层无法检测的结构性缺陷
        metrics = self._compute_metrics(note_text, source_text, body_text)
        metric_guardrail_issues = []

        # 护栏 1: 信息密度过低 → 自动失败（笔记几乎没有实质内容）
        # 注意: info_density 基于 2-4 字中文词组的多样性/句数比，
        # 空话笔记可能仍有较高值（jieba 分词会拆出大量2字词），
        # 所以阈值设为 0.15，只在极端情况下触发
        if metrics.info_density < 0.15:
            overall_passed = False
            metric_guardrail_issues.append(Issue(
                rule_id="M1", rule_name="信息密度护栏",
                severity="fatal",
                line_range="全文",
                description=f"信息密度 {metrics.info_density:.2f} < 0.30 下限，笔记缺乏实质内容",
                suggestion="增加具体概念、数据、框架；减少空泛描述",
            ))

        # 护栏 2: 引用比过高 → 分数封顶（疑似照搬原文）
        if metrics.quote_ratio > 0.5:
            if total_score > 0.70:
                total_score = 0.70
            overall_passed = False
            metric_guardrail_issues.append(Issue(
                rule_id="M2", rule_name="引用比护栏",
                severity="major",
                line_range="全文",
                description=f"原话引用比 {metrics.quote_ratio:.2f} > 0.50 上限，笔记照搬原文过多",
                suggestion="减少直接引用，增加提炼和结构化整理",
            ))

        # 护栏 3: 压缩比异常 → 失败（过长或过短）
        # 压缩比 = 笔记字数/原文字数，10-30% 是理想范围
        # > 1.0 意味着笔记比原文还长（严重冗余），< 0.03 意味着几乎没内容
        if metrics.compression_ratio > 1.0:
            overall_passed = False
            metric_guardrail_issues.append(Issue(
                rule_id="M3", rule_name="压缩比护栏",
                severity="major",
                line_range="全文",
                description=f"压缩比 {metrics.compression_ratio:.2f} > 1.00，笔记比原文还长（严重冗余）",
                suggestion="进一步提炼，目标压缩比 10-30%",
            ))
        elif metrics.compression_ratio < 0.03:
            overall_passed = False
            metric_guardrail_issues.append(Issue(
                rule_id="M3", rule_name="压缩比护栏",
                severity="major",
                line_range="全文",
                description=f"压缩比 {metrics.compression_ratio:.2f} < 0.03，笔记过短（可能遗漏大量内容）",
                suggestion="补充遗漏的重要议题和概念",
            ))

        # 护栏 4: 结构丰富度过低 → 失败（笔记缺乏结构化元素）
        if metrics.structure_score < 0.20:
            overall_passed = False
            metric_guardrail_issues.append(Issue(
                rule_id="M4", rule_name="结构丰富度护栏",
                severity="major",
                line_range="全文",
                description=f"结构丰富度 {metrics.structure_score:.2f} < 0.20，笔记缺乏结构化元素（标题/列表/表格/引用）",
                suggestion="增加标题分层、要点列表、表格等结构化元素",
            ))

        # 将护栏结果加入 rule_results（便于报告展示）
        for guardrail_id in ["M1", "M2", "M3", "M4"]:
            guardrail_issues = [i for i in metric_guardrail_issues if i.rule_id == guardrail_id]
            if guardrail_issues:
                guardrail_name = guardrail_issues[0].rule_name
                guardrail_severity = guardrail_issues[0].severity
                results[guardrail_id] = RuleResult(
                    guardrail_id, guardrail_name,
                    0.0 if guardrail_severity == "fatal" else 0.5,
                    False,
                    guardrail_issues,
                )

        # 汇总 issues（规则 + 护栏）
        all_issues = []
        for rid in self.RULE_WEIGHTS:
            if rid in results:
                all_issues.extend(results[rid].issues)
        all_issues.extend(metric_guardrail_issues)
        fatal_count = sum(1 for i in all_issues if i.severity == "fatal")
        major_count = sum(1 for i in all_issues if i.severity == "major")
        medium_count = sum(1 for i in all_issues if i.severity == "medium")

        summary = (
            f"总分: {total_score:.2%} | "
            f"{'✅ 通过' if overall_passed else '❌ 未通过'} | "
            f"致命:{fatal_count} 严重:{major_count} 中等:{medium_count}"
        )

        # 条件 LLM 评审：仅在边界分数时触发
        # 触发条件：llm_eval_on_borderline=True 且分数在 [low, high] 区间
        llm_eval_result = None
        if (self._llm_eval_on_borderline
                and self._llm_eval_provider is not None
                and self._llm_eval_borderline_low <= total_score < self._llm_eval_borderline_high):
            try:
                llm_eval_result = self.llm_evaluate(
                    note_text, source_text, self._llm_eval_provider
                )
                if llm_eval_result and isinstance(llm_eval_result, LLMEvalResult):
                    logger.info(
                        f"LLM 评审触发 (borderline {total_score:.2%}): "
                        f"overall={llm_eval_result.overall_score:.1f}/5"
                    )
                elif isinstance(llm_eval_result, EvalFailure):
                    # B1: 根据 failure_class 路由，而非字符串匹配
                    fc = llm_eval_result.failure_class
                    if fc == FailureClass.RETRYABLE:
                        logger.warning(
                            f"LLM 评审瞬态失败 (borderline {total_score:.2%}): "
                            f"reason={llm_eval_result.reason}，可重试"
                        )
                    elif fc == FailureClass.TERMINAL:
                        logger.error(
                            f"LLM 评审永久失败 (borderline {total_score:.2%}): "
                            f"reason={llm_eval_result.reason}，不可重试"
                        )
                    elif fc == FailureClass.DEGRADED:
                        logger.warning(
                            f"LLM 评审降级输出 (borderline {total_score:.2%}): "
                            f"reason={llm_eval_result.reason}，使用启发式分数"
                        )
            except Exception as e:
                logger.warning(f"条件 LLM 评审失败: {e}")

        return QualityReport(
            note_text=note_text, source_text=source_text,
            note_label=note_label, source_label=source_label,
            total_score=total_score,
            rule_results=results,
            overall_passed=overall_passed,
            summary=summary,
            metrics=metrics,
            llm_eval=llm_eval_result,
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
            "R5": lambda n, s: rules.check_coverage(n, s),
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
    # 状态: CLI --llm-eval 可触发，但未集成到主流水线（evaluate_text 不调用）
    # 待办: 需验证 4 维度评分的可靠性和成本效益后才考虑集成
    # 已知问题: faithfulness 维度存在同模型自评循环风险
    # ----------------------------------------------------------
    def llm_evaluate(self, note_text: str, source_text: str,
                     provider=None) -> Optional[Union[LLMEvalResult, EvalFailure]]:
        """
        用 LLM 对笔记做多维度深度评审

        注意: 此方法仅通过 CLI --llm-eval 参数触发，不影响主流水线的
        pass/fail 决策。集成到主流水线前需完成：
        1) 评分可靠性验证（同输入多次评分 CV < 15%）
        2) 与人工评分的相关性验证（ρ > 0.6）
        3) faithfulness 维度的循环论证问题解决
        4) 成本效益分析（实际 $0.015-0.03/文档）

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
3. **忠实度** (faithfulness): 逐句检查笔记中的数字、百分比、人名、因果论断是否与原文一致。
4. **可行动性** (actionability): 行动清单是否具体可执行？洞察是否可迁移？

## 通用评分校准
- 5分: 优秀，无任何问题
- 4分: 良好，有微小瑕疵
- 3分: 及格，有明显但非致命问题
- 2分: 不及格，有严重问题
- 1分: 极差，几乎不可用

## 忠实度(faithfulness)特殊校准
笔记是对原文的提炼和压缩，不是逐字翻译。评分时请区分：
- 5分: 所有关键事实、数字、人名准确无误，无编造内容
- 4分: 有轻微的表述简化，但核心事实准确
- 3分: 存在将模糊口语精确化的情况（如"十几亿"→"十三亿"），但不影响核心论点
- 2分: 存在原文没有的具体数据或因果论断，但非主观编造
- 1分: 有明显的虚构或与原文矛盾的内容

注意：将口语化模糊表述（如"大几百万""六十多"）转化为精确数字是笔记整理的正常行为，
不应过度扣分。只有当转化后的数字与原文语义明显矛盾时才应扣分。

## 输出格式（严格 JSON）
```json
{{
  "richness": 4.0,
  "readability": 3.5,
  "faithfulness": 5.0,
  "actionability": 3.0,
  "overall": 3.9,
  "feedback": "一句话总评",
  "suggestions": ["建议1", "建议2"]
}}
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

            # P0 防御：检查是否返回了拒绝文本
            from noteforge.core.llm_providers import LLMProvider
            if isinstance(provider, LLMProvider) and provider._is_content_filtered(result):
                return EvalFailure(
                    reason="content_filter",
                    raw_response=result[:500],
                    provider=provider.get_name() if hasattr(provider, 'get_name') else "unknown",
                    failure_class=FailureClass.DEGRADED,
                )

            # 解析 JSON — 支持多种格式
            # 1. 去除 markdown 代码块标记
            cleaned = re.sub(r'^```(?:json)?\s*', '', result.strip())
            cleaned = re.sub(r'\s*```\s*$', '', cleaned.strip())

            # 2. 提取 JSON 对象（支持嵌套）
            json_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                except json.JSONDecodeError:
                    # 回退：尝试修复常见的 JSON 错误（尾逗号、注释）
                    fixed = re.sub(r',\s*([}\]])', r'\1', json_match.group())
                    fixed = re.sub(r'//[^\n]*', '', fixed)
                    try:
                        data = json.loads(fixed)
                    except json.JSONDecodeError:
                        # P0: 修复后仍失败 → 返回结构化错误而非 None
                        logger.error(
                            f"LLM 评审 JSON 解析失败（修复尝试后仍无效）: "
                            f"raw={result[:200]}"
                        )
                        return EvalFailure(
                            reason="json_parse",
                            raw_response=result[:500],
                            provider=provider.get_name() if hasattr(provider, 'get_name') else "unknown",
                            failure_class=FailureClass.RETRYABLE,
                        )
            else:
                # 无 JSON 对象 → 结构化错误
                logger.error(f"LLM 评审返回中无 JSON 对象: raw={result[:200]}")
                return EvalFailure(
                    reason="json_parse",
                    raw_response=result[:500],
                    provider=provider.get_name() if hasattr(provider, 'get_name') else "unknown",
                    failure_class=FailureClass.RETRYABLE,
                )

            if data:
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
            return EvalFailure(
                reason="other",
                raw_response=str(e)[:500],
                provider=provider.get_name() if hasattr(provider, 'get_name') else "unknown",
                failure_class=FailureClass.TERMINAL,
            )

        # 不应到达此处，但作为最终安全网
        return EvalFailure(
            reason="other",
            raw_response="llm_evaluate reached unreachable code",
            failure_class=FailureClass.TERMINAL,
        )
