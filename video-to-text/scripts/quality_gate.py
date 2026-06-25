# ============================================================
# 笔记质量自动评分脚本 v1.0
# 用途: 对生成的笔记做6维质量评分，输出校验报告
# 用法: python quality_gate.py <笔记文件> <原文文件> [-v]
# 输出: QualityReport(总分/项分/问题清单)
# ============================================================

import os
import re
import sys
import json
import logging
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger('noteforge.quality')


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
class QualityMetrics:
    """启发式质量指标（零 API 成本）"""
    compression_ratio: float    # 笔记字数/原文字数（理想 10-30%）
    structure_score: float      # 结构丰富度（0-1）
    info_density: float         # 信息密度（0-1）
    readability_score: float    # 可读性（0-1）
    quote_ratio: float          # 原话引用占比（0-1）
    action_specificity: float   # 行动清单具体性（0-1）
    overall_richness: float     # 综合丰富度（加权平均）

    def to_dict(self):
        return {
            "compression_ratio": round(self.compression_ratio, 3),
            "structure_score": round(self.structure_score, 2),
            "info_density": round(self.info_density, 2),
            "readability_score": round(self.readability_score, 2),
            "quote_ratio": round(self.quote_ratio, 3),
            "action_specificity": round(self.action_specificity, 2),
            "overall_richness": round(self.overall_richness, 2),
        }


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
    note_path: str
    source_path: str
    total_score: float
    rule_results: Dict[str, RuleResult]
    overall_passed: bool
    summary: str
    metrics: Optional[QualityMetrics] = None
    llm_eval: Optional[LLMEvalResult] = None

    def to_dict(self):
        result = {
            "note_path": self.note_path,
            "source_path": self.source_path,
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
        if self.metrics:
            result["metrics"] = self.metrics.to_dict()
        if self.llm_eval:
            result["llm_eval"] = self.llm_eval.to_dict()
        return result


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
    CONTENT_TYPE_DOMAINS = {
        'lecture': ['finance'],
        'tutorial': ['short_video'],
        'interview': ['geopolitics'],
        'podcast': ['geopolitics'],
        'meeting': [],
    }

    def __init__(self, rules_path: Optional[str] = None,
                 content_type: Optional[str] = None):
        """
        Args:
            rules_path: note_generation_rules.yaml 路径（可选，用于加载 KEY_CONCEPTS 配置）
            content_type: 内容类型（决定加载哪些领域的概念）
        """
        self._key_concepts = dict(self.DEFAULT_KEY_CONCEPTS)
        self._content_type = content_type
        if rules_path and os.path.exists(rules_path):
            try:
                import yaml
                with open(rules_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f) or {}
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
            except Exception:
                pass  # 回退到内置默认

    def evaluate(self, note_path: str, source_path: str) -> QualityReport:
        """评估笔记质量"""
        note_text = self._read_file(note_path)
        source_text = self._read_file(source_path)

        # 硬校验: 笔记正文长度（排除标题、元数据、分隔线）
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
            # 内容过短（可能被 API 安全过滤或生成失败），直接判定不合格
            return QualityReport(
                note_path=note_path,
                source_path=source_path,
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

        results = {}
        results["R0"] = RuleResult("R0", "内容完整性", 1.0, True)  # 通过长度校验
        results["R1"] = self._check_fabricated_data(note_text, source_text)
        results["R2"] = self._check_unmarked_additions(note_text, source_text)
        results["R3"] = self._check_semantic_reversal(note_text, source_text)
        results["R4"] = self._check_concept_distortion(note_text)
        results["R5"] = self._check_coverage(note_text, source_text)
        results["R6"] = self._check_consistency(note_text)
        results["R7"] = self._check_framework_completeness(note_text)
        results["R8"] = self._check_insight_actionability(note_text)
        results["R9"] = self._check_layering_accuracy(note_text)
        results["R10"] = self._check_timeline_accuracy(note_text, source_text)
        results["R11"] = self._check_quote_attribution(note_text, source_text)
        results["R12"] = self._check_name_number_consistency(note_text, source_text)

        # 计算加权总分
        total_weight = sum(self.RULE_WEIGHTS.values())
        total_score = sum(
            results[rid].score * self.RULE_WEIGHTS[rid]
            for rid in self.RULE_WEIGHTS
            if rid in results
        ) / total_weight

        # 致命规则必须全部通过
        fatal_passed = all(
            results[rid].passed
            for rid in ["R1", "R2", "R3", "R5"]
            if rid in results
        )

        overall_passed = total_score >= 0.80 and fatal_passed

        # 生成摘要
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

        # 计算启发式质量指标
        metrics = self._compute_metrics(note_text, source_text, body_text)

        return QualityReport(
            note_path=note_path,
            source_path=source_path,
            total_score=total_score,
            rule_results=results,
            overall_passed=overall_passed,
            summary=summary,
            metrics=metrics,
        )

    # ----------------------------------------------------------
    # 启发式质量指标（零 API 成本）
    # ----------------------------------------------------------
    def _compute_metrics(self, note_text: str, source_text: str,
                         body_text: str) -> QualityMetrics:
        """计算启发式质量指标"""
        lines = note_text.split('\n')
        total_lines = len([l for l in lines if l.strip()])

        # 1. 压缩比：笔记字数/原文字数（理想 10-30%）
        note_chars = len(body_text.replace('\n', '').replace(' ', ''))
        source_chars = len(source_text.replace('\n', '').replace(' ', ''))
        compression = note_chars / max(source_chars, 1)

        # 2. 结构丰富度（标题数、列表数、表格数、引用数）
        headings = sum(1 for l in lines if re.match(r'^#{1,6}\s', l))
        lists = sum(1 for l in lines if re.match(r'^\s*[-*]\s', l))
        tables = sum(1 for l in lines if l.strip().startswith('|') and '|' in l[1:])
        quotes = sum(1 for l in lines if l.strip().startswith('>'))
        # 归一化：理想笔记应有 5-15 个标题、10-30 个列表项、0-5 个表格、2-8 个引用
        structure = min(1.0, (
            min(headings / 8, 1.0) * 0.3 +
            min(lists / 15, 1.0) * 0.3 +
            min(tables / 3, 1.0) * 0.2 +
            min(quotes / 4, 1.0) * 0.2
        ))

        # 3. 信息密度（不同概念/总句数）
        sentences = [l.strip() for l in lines if len(l.strip()) > 10]
        # 提取中文词组（2-4字）作为概念代理
        all_words = []
        for s in sentences:
            all_words.extend(re.findall(r'[一-鿿]{2,4}', s))
        unique_words = set(all_words)
        density = min(1.0, len(unique_words) / max(len(sentences) * 2, 1))

        # 4. 可读性（段落质量 + 结构多样性 + 信息密度）
        # 智能段落检测：按内容逻辑分组（列表组、表格组、引用组各算一个段落）
        paragraphs = []
        current_para = []
        current_type = 'text'  # text / list / table / quote

        for l in lines:
            stripped = l.strip()
            if not stripped:
                if current_para:
                    paragraphs.append((current_type, current_para))
                    current_para = []
                    current_type = 'text'
                continue

            # 判断行类型
            if stripped.startswith('- ') or stripped.startswith('* '):
                line_type = 'list'
            elif stripped.startswith('|'):
                line_type = 'table'
            elif stripped.startswith('>'):
                line_type = 'quote'
            elif stripped.startswith('#'):
                line_type = 'heading'
            else:
                line_type = 'text'

            # 类型切换时结束当前段落（但连续同类型列表/表格/引用合并）
            if current_type != line_type and not (
                current_type == 'list' and line_type == 'list'
            ):
                if current_para:
                    paragraphs.append((current_type, current_para))
                    current_para = []

            current_type = line_type
            current_para.append(stripped)

        if current_para:
            paragraphs.append((current_type, current_para))

        if paragraphs:
            # 只评估文本段落的长度（列表/表格/引用不参与段落长度评分）
            text_paras = [p for t, p in paragraphs if t == 'text' and len(p) >= 2]
            if text_paras:
                ideal_count = sum(1 for p in text_paras if 3 <= len(p) <= 8)
                long_count = sum(1 for p in text_paras if len(p) > 10)
                para_quality = (
                    ideal_count / len(text_paras) * 0.6
                    + max(0, 1 - long_count / len(text_paras)) * 0.4
                )
            else:
                para_quality = 0.8  # 纯列表/表格笔记（如实操步骤）

            # 结构多样性：标题、列表、表格、引用混合使用
            type_set = set(t for t, _ in paragraphs)
            structure_variety = min(1.0, len(type_set) / 3.0)

            # 内容密度：非空段落中有实质内容的比例
            substantial = sum(1 for t, p in paragraphs if len(p) >= 2 or t in ('list', 'table'))
            density_per_para = substantial / max(len(paragraphs), 1)

            readability = (
                para_quality * 0.45 +
                structure_variety * 0.30 +
                density_per_para * 0.25
            )

        # 5. 原话引用占比（引号/引用块行数/总行数）
        quote_lines = sum(
            1 for l in lines
            if l.strip().startswith('>')
            or '"' in l or '"' in l or '「' in l
        )
        quote_ratio = quote_lines / max(total_lines, 1)

        # 6. 行动清单具体性（支持复选框、数字编号、列表三种格式）
        action_patterns = [
            r'^\s*-\s*\[[ x]\]\s*',          # - [ ] 格式
            r'^\s*\d+[.、]\s*(?=[一-鿿])',  # 1. 格式（后跟中文）
            r'^\s*[-*]\s+(?:练习|建立|制作|阅读|学习|尝试|使用|分析|拆解|记录|观察|关注)',
        ]
        # 先找到行动清单段落
        in_action_section = False
        action_lines = []
        for l in lines:
            if '行动清单' in l or '行动项' in l or '行动建议' in l:
                in_action_section = True
                continue
            if in_action_section:
                if l.startswith('##') or l.startswith('---'):
                    in_action_section = False
                    continue
                if l.strip() and any(re.match(p, l) for p in action_patterns):
                    action_lines.append(l)
                elif l.strip() and not l.startswith('#') and not l.startswith('>'):
                    # 段落内的行动描述也收集
                    if any(v in l for v in ['练习', '建立', '制作', '阅读', '尝试', '每周', '每天', '每月']):
                        action_lines.append(l)
        if action_lines:
            # 具体行动：包含数字、时间、频率、工具名
            concrete_patterns = [
                r'\d+\s*(?:小时|分钟|天|周|月|次|篇|条)',
                r'(?:每天|每周|每月|每次|定期)',
                r'(?:建立|制作|创建|使用|打开|检查)',
            ]
            concrete_count = sum(
                1 for a in action_lines
                if any(re.search(p, a) for p in concrete_patterns)
            )
            action_specificity = concrete_count / len(action_lines)
        else:
            action_specificity = 0.0

        # 综合丰富度（加权平均）
        overall = (
            min(compression / 0.2, 1.0) * 0.15 +  # 压缩比（达到20%即满分）
            structure * 0.25 +
            density * 0.20 +
            readability * 0.20 +
            min(quote_ratio / 0.1, 1.0) * 0.10 +  # 引用占比（达到10%即满分）
            action_specificity * 0.10
        )

        return QualityMetrics(
            compression_ratio=compression,
            structure_score=structure,
            info_density=density,
            readability_score=readability,
            quote_ratio=quote_ratio,
            action_specificity=action_specificity,
            overall_richness=overall,
        )

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

    # ----------------------------------------------------------
    # R1: 禁止虚构数据
    # ----------------------------------------------------------
    def _check_fabricated_data(self, note_text: str, source_text: str) -> RuleResult:
        """扫描笔记中的数字/百分比，检查原文是否有出处"""
        issues = []

        for pattern in self.FABRICATED_PATTERNS:
            for match in re.finditer(pattern, note_text):
                number_context = match.group()
                line_num = note_text[:match.start()].count('\n') + 1

                # 跳过时间戳格式（2:30, 5:50, 01:23:45 等）
                if re.match(r'^[\d.]+\s*[：:]\s*\d+', number_context):
                    continue
                # 跳过 Markdown 引用中的数字
                ctx_start = max(0, match.start() - 20)
                ctx_prefix = note_text[ctx_start:match.start()]
                if ctx_prefix.rstrip().endswith('>'):
                    continue

                # 在原文中查找该数字（智能匹配）
                if not self._number_in_source(number_context, source_text):
                    issues.append(Issue(
                        rule_id="R1",
                        rule_name="禁止虚构数据",
                        severity="fatal",
                        line_range=f"L{line_num}",
                        description=f"疑似虚构数据: '{number_context}' 在原文中未找到出处",
                        suggestion="删除该数字或标注原文出处"
                    ))

        score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.25)
        return RuleResult("R1", "禁止虚构数据", score, len(issues) == 0, issues)

    # ----------------------------------------------------------
    # R2: 禁止越界增补
    # ----------------------------------------------------------
    def _check_unmarked_additions(self, note_text: str, source_text: str) -> RuleResult:
        """检测笔记中是否有未标注补充分割的内容"""
        issues = []

        # 检查是否有"[📝笔者补充]"标记
        has_mark = "[📝笔者补充]" in note_text or "[📝补充]" in note_text

        # 检测可能有增补嫌疑的段落（建议/策略类内容）
        suspicion_patterns = [
            (r'✅\s*(?:短期|中期|长期)\s*(?:应对|策略|建议).*?(?=\n\n|\Z)', '策略建议类内容'),
            (r'(?:📈|📊)\s*(?:短期|中期|长期)\s*(?:布局|规划|策略).*?(?=\n\n|\Z)', '策略规划类内容'),
            (r'(?:应对|行动|实施)\s*(?:策略|方案|建议)\s*(?:建议|清单|指南)', '策略建议标题'),
            (r'(?:综上|总之|总结).*(?:建议|推荐|应该)\s*(?:采取|执行|实施).*?(?=\n\n|\Z)', '总结性建议'),
        ]

        for pattern, desc in suspicion_patterns:
            for match in re.finditer(pattern, note_text, re.DOTALL):
                matched_text = match.group()
                if matched_text not in source_text:
                    line_num = note_text[:match.start()].count('\n') + 1
                    if not has_mark:
                        mark_label = "[📝笔者补充]"
                        issues.append(Issue(
                            rule_id="R2",
                            rule_name="禁止越界增补",
                            severity="fatal",
                            line_range=f"L{line_num}",
                            description=f"疑似越界增补({desc}): 此内容在原文中未找到，且无{mark_label}标记",
                            suggestion=f"如确为补充内容，请添加{mark_label}标记；如非必要，请删除"
                        ))

        score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.3)
        return RuleResult("R2", "禁止越界增补", score, len(issues) == 0, issues)

    # ----------------------------------------------------------
    # R3: 禁止事实反转
    # ----------------------------------------------------------
    def _check_semantic_reversal(self, note_text: str, source_text: str) -> RuleResult:
        """检测可能的语义反转（半自动：标记可疑模式供人工复查）"""
        issues = []

        # 已知的历史反转模式
        reversal_patterns = [
            ("可解释性[强高好]", "可解释性弱|不可解释"),
            ("黑箱", "不是黑箱|非黑箱"),
            ("逆向思维", "基本面趋势|右侧投资"),
            ("量化优于.*主观", "不可解释.*因子|模型训练"),  # 量化可解释性强于主观 类表述
        ]

        for note_pattern, expected_source_pattern in reversal_patterns:
            if re.search(note_pattern, note_text):
                if not re.search(expected_source_pattern, source_text):
                    for match in re.finditer(note_pattern, note_text):
                        line_num = note_text[:match.start()].count('\n') + 1
                        issues.append(Issue(
                            rule_id="R3",
                            rule_name="禁止事实反转",
                            severity="fatal",
                            line_range=f"L{line_num}",
                            description=f"疑似语义反转: 笔记中出现'{match.group()}'，但原文中未找到对应表述",
                            suggestion="请人工核实该论点是否与原文语义方向一致"
                        ))

        score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.3)
        return RuleResult("R3", "禁止事实反转", score, len(issues) == 0, issues)

    # ----------------------------------------------------------
    # R4: 禁止关键概念简化失真
    # ----------------------------------------------------------
    def _check_concept_distortion(self, note_text: str) -> RuleResult:
        """检查笔记中的专业概念是否保留了关键限定词"""
        issues = []

        for concept, required_keywords in self._key_concepts.items():
            if concept in note_text:
                # 找到概念出现的上下文（前后各200字符）
                for match in re.finditer(re.escape(concept), note_text):
                    start = max(0, match.start() - 200)
                    end = min(len(note_text), match.end() + 200)
                    context = note_text[start:end]

                    # 计算上下文丰富度：如果说明文字足够多（>100字），说明概念在被深度讨论
                    context_length = len(context.replace(concept, '').strip())

                    # 检查必有关键词
                    missing = [
                        kw for kw in required_keywords
                        if kw not in context
                    ]

                    if missing:
                        # 上下文丰富时（概念正在被深度讨论），降低严重度
                        # 只在上下文很短（<50字，可能只是简单提及）时才报告
                        if context_length < 50:
                            line_num = note_text[:match.start()].count('\n') + 1
                            issues.append(Issue(
                                rule_id="R4",
                                rule_name="禁止关键概念简化失真",
                                severity="major",
                                line_range=f"L{line_num}",
                                description=f"概念'{concept}'丢失关键限定词: {', '.join(missing)}",
                                suggestion=f"请在'{concept}'的描述中补充: {', '.join(missing)}"
                            ))

        score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.15)
        return RuleResult("R4", "禁止关键概念简化失真", score, len(issues) == 0, issues)

    # ----------------------------------------------------------
    # R5: 覆盖度底线
    # ----------------------------------------------------------
    def _check_coverage(self, note_text: str, source_text: str) -> RuleResult:
        """检查笔记覆盖了原文多少议题"""
        issues = []

        # 从原文中提取关键议题（章节标题、关键段落标记）
        chapter_patterns = [
            # 听悟格式: **HH:MM 标题**
            r'\*\*(\d{2}:\d{2}\s+.+?)\*\*',
            # 通用标题格式（排除元数据行）
            r'^#{1,3}\s+(?!(?:转写|笔记|学习|课程|第\d+集|视频|来源|时间|格式))(.+)$',
        ]

        source_chapters = []
        for pattern in chapter_patterns:
            source_chapters.extend(re.findall(pattern, source_text, re.MULTILINE))

        # 去重
        source_chapters = list(dict.fromkeys(source_chapters))

        if not source_chapters:
            # 无章节标记时，关键词覆盖率不可靠（原始转写 vs 提炼笔记用词差异大）
            # 直接通过，由 R7(框架完整性) 和 R8(洞察可行动性) 间接保证质量
            ratio = 1.0
        else:
            # 检查每个章节标题（取前15个字符作为关键词）
            covered = 0
            for chapter in source_chapters:
                chapter_key = chapter[:15].strip()
                if chapter_key and chapter_key in note_text:
                    covered += 1
                elif any(kw in note_text for kw in chapter_key.split()[:4]):
                    covered += 1
            ratio = covered / len(source_chapters)

        # 双阈值: < 30% fatal（严重缺失），< 80% major（一般缺失）
        if ratio < 0.30:
            issues.append(Issue(
                rule_id="R5",
                rule_name="覆盖度底线",
                severity="fatal",
                line_range="全文",
                description=f"笔记覆盖率为 {ratio:.1%}，严重低于30%下限。原文约{len(source_chapters) if source_chapters else 'N/A'}个议题，笔记仅覆盖约{int(ratio * len(source_chapters))}个",
                suggestion="笔记可能为空或严重不完整，请检查 LLM 生成是否成功"
            ))
        elif ratio < 0.80:
            issues.append(Issue(
                rule_id="R5",
                rule_name="覆盖度底线",
                severity="major",
                line_range="全文",
                description=f"笔记覆盖率为 {ratio:.1%}，低于80%底线。原文约{len(source_chapters) if source_chapters else 'N/A'}个议题，笔记仅覆盖约{int(ratio * len(source_chapters))}个",
                suggestion="请对照原文章节列表检查遗漏的议题并补充"
            ))

        return RuleResult(
            "R5", "覆盖度底线",
            min(1.0, ratio / 0.80),
            ratio >= 0.30,  # fatal 阈值 30%
            issues
        )

    # ----------------------------------------------------------
    # R6: 术语一致性
    # ----------------------------------------------------------
    def _check_consistency(self, note_text: str) -> RuleResult:
        """检查笔记中术语表的定义是否与正文使用一致"""
        issues = []

        # 查找术语表区域
        term_table_pattern = r'\|.*?\|.*?\|'  # Markdown表格行
        term_tables = list(re.finditer(term_table_pattern, note_text, re.MULTILINE))

        if term_tables:
            # 提取术语表中的术语名和定义
            table_terms = {}
            for match in term_tables:
                row = match.group()
                parts = [p.strip() for p in row.split('|') if p.strip()]
                if len(parts) >= 2:
                    term = parts[0].replace('**', '').strip()
                    definition = parts[1].replace('**', '').strip()
                    if term and definition and term not in ('术语', '解释', '---', '术语表'):
                        table_terms[term] = definition

            # 检查正文中对这些术语的使用
            body_text = note_text
            for table_match in term_tables:
                body_text = body_text.replace(table_match.group(), '')

            for term, definition in table_terms.items():
                if term in body_text:
                    # 检查定义中的核心词是否与正文语境一致
                    def_keywords = set(
                        w for w in definition.replace('(', ' ').replace(')', ' ').split()
                        if len(w) >= 2
                    )
                    # 对于"因子=不可解释为主"这类，检查正文是否出现矛盾的"可解释性[强高]"
                    if "不可解释" in definition and re.search(r'可解释性\s*[强高]', body_text):
                        issues.append(Issue(
                            rule_id="R6",
                            rule_name="术语一致性",
                            severity="medium",
                            line_range="术语表/正文",
                            description=f"术语'{term}'定义为'{definition}'，但正文中出现了矛盾表述",
                            suggestion="请检查术语定义与正文描述是否一致"
                        ))

        score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.2)
        return RuleResult("R6", "术语一致性", score, len(issues) == 0, issues)

    # ----------------------------------------------------------
    # R7: 框架完整性
    # ----------------------------------------------------------
    def _check_framework_completeness(self, note_text: str) -> RuleResult:
        """检查笔记中提取的框架是否保留了全部组成要素"""
        issues = []

        # 检测框架类段落（包含"步骤"、"要素"、"阶段"、"法"等关键词的列表）
        framework_markers = [
            (r'(?:第[一二三四五六七八九十\d]+步)', '步骤'),
            (r'(?:第[一二三四五六七八九十\d]+[点个阶段])', '阶段'),
            (r'(?:\d+[.、])\s*\S+', '编号列表'),
        ]

        # 检查是否有框架段落但要素过少
        for pattern, label in framework_markers:
            matches = re.findall(pattern, note_text)
            if len(matches) >= 5:
                # 找到一个有 5+ 要素的框架，检查是否有对应的详细说明
                # 如果框架步骤很多但每步描述很短（<20字），可能丢失了细节
                framework_section = self._extract_framework_section(note_text, pattern)
                if framework_section:
                    short_steps = sum(
                        1 for line in framework_section.split('\n')
                        if re.match(r'\s*(?:\d+[.、]|第)', line.strip())
                        and len(line.strip()) < 20
                    )
                    total_steps = sum(
                        1 for line in framework_section.split('\n')
                        if re.match(r'\s*(?:\d+[.、]|第)', line.strip())
                    )
                    if total_steps > 0 and short_steps / total_steps > 0.5:
                        issues.append(Issue(
                            rule_id="R7",
                            rule_name="框架完整性",
                            severity="major",
                            line_range="框架段落",
                            description=f"框架包含 {total_steps} 个要素，但 {short_steps} 个描述过于简短（<20字），可能丢失关键细节",
                            suggestion="请为每个框架要素补充足够的描述，保留原文的关键限定词和条件"
                        ))

        score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.25)
        return RuleResult("R7", "框架完整性", score, len(issues) == 0, issues)

    def _extract_framework_section(self, text: str, pattern: str) -> str:
        """提取包含框架的段落（从第一个匹配到最后一个匹配之间的文本）"""
        matches = list(re.finditer(pattern, text))
        if len(matches) < 3:
            return ""
        start = max(0, matches[0].start() - 100)
        end = min(len(text), matches[-1].end() + 100)
        return text[start:end]

    # ----------------------------------------------------------
    # R8: 洞察可行动性
    # ----------------------------------------------------------
    def _check_insight_actionability(self, note_text: str) -> RuleResult:
        """检查洞察是否包含具体行动指引"""
        issues = []

        # 检测空洞总结模式（只有方向性描述，没有具体行动）
        vague_patterns = [
            (r'(?:要|应该|需要)\s*(?:重视|关注|注意|加强|提升|提高)\s*\S{2,6}',
             '空洞的方向性总结'),
            (r'(?:关键是|重要的是|核心是)\s*(?:要|应该)\s*\S{2,6}',
             '缺乏具体步骤的断言'),
        ]

        # 只检查"洞察"相关段落
        insight_sections = re.findall(
            r'(?:洞察|行动|建议|总结).*?(?=\n##|\n---|\Z)',
            note_text, re.DOTALL
        )

        for section in insight_sections:
            for pattern, desc in vague_patterns:
                for match in re.finditer(pattern, section):
                    # 检查该行后面是否有具体行动（如"怎么做"）
                    line_start = section.rfind('\n', 0, match.start()) + 1
                    line_end = section.find('\n', match.end())
                    if line_end == -1:
                        line_end = len(section)
                    full_line = section[line_start:line_end]

                    # 如果行长度很短且没有具体动词，标记为问题
                    action_verbs = ['执行', '完成', '检查', '记录', '列出',
                                    '练习', '使用', '按照', '通过']
                    has_action = any(v in full_line for v in action_verbs)
                    if len(full_line) < 50 and not has_action:
                        line_num = note_text[:note_text.find(section) + match.start()].count('\n') + 1
                        issues.append(Issue(
                            rule_id="R8",
                            rule_name="洞察可行动性",
                            severity="major",
                            line_range=f"L{line_num}",
                            description=f"疑似空洞总结: '{full_line[:60]}'",
                            suggestion="请补充具体行动步骤：做什么、何时做、预期效果"
                        ))

        score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.2)
        return RuleResult("R8", "洞察可行动性", score, len(issues) == 0, issues)

    # ----------------------------------------------------------
    # R9: 分层准确性
    # ----------------------------------------------------------
    def _check_layering_accuracy(self, note_text: str) -> RuleResult:
        """检查是否正确区分表面内容和可迁移知识"""
        issues = []

        # 检测将个案包装为通用原则的模式
        generalization_patterns = [
            (r'(?:所有|任何|每个|凡是)\s*(?:人|创作者|导演|学习者)\s*(?:都|应该|必须)',
             '过度泛化'),
            (r'(?:永远|绝对|一定)\s*(?:要|不能|不要)',
             '绝对化表述'),
        ]

        # 引用上下文模式（排除引用原话的场景）
        quote_patterns = [
            r'[「」]',                  # 日文引号
            r'"[^"]*"',                # 英文双引号
            r"'[^']*'",               # 英文单引号
            r'>\s*["「\']',            # Markdown 引用块
            r'(?:讲师|老师|嘉宾|主持人|他说|她说|原文)[说道讲认为]:?\s*',
        ]
        quote_re = re.compile('|'.join(quote_patterns))

        for pattern, desc in generalization_patterns:
            for match in re.finditer(pattern, note_text):
                line_num = note_text[:match.start()].count('\n') + 1

                # 检查是否在引用上下文中（更大窗口 400 字符）
                broader_start = max(0, match.start() - 400)
                broader_context = note_text[broader_start:match.end() + 100]
                # 如果附近有引号标记，可能是讲师原话，降低严重度
                is_in_quote = bool(quote_re.search(broader_context))

                # 检查上下文是否标注了适用范围
                context_start = max(0, match.start() - 200)
                context = note_text[context_start:match.end() + 100]
                scope_markers = ['在.*场景', '对于.*来说', '在.*情况下',
                                 '适用于', '限于', '主要']
                has_scope = any(re.search(m, context) for m in scope_markers)

                if not has_scope:
                    if is_in_quote:
                        # 引用原话中的泛化表述，降级为 medium，加提示
                        issues.append(Issue(
                            rule_id="R9",
                            rule_name="分层准确性",
                            severity="medium",
                            line_range=f"L{line_num}",
                            description=f"引用中的泛化表述({desc}): '{match.group()}'（可能是讲师原话，请确认是否需要添加适用范围说明）",
                            suggestion="如为讲师原话可保留，但建议在后续段落标注适用范围"
                        ))
                    else:
                        issues.append(Issue(
                            rule_id="R9",
                            rule_name="分层准确性",
                            severity="medium",
                            line_range=f"L{line_num}",
                            description=f"疑似过度泛化({desc}): '{match.group()}'",
                            suggestion="请标注适用范围和限制条件，区分个案经验和通用原则"
                        ))

        score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.15)
        return RuleResult("R9", "分层准确性", score, len(issues) == 0, issues)

    # ----------------------------------------------------------
    # R10: 时间线准确性
    # ----------------------------------------------------------
    def _check_timeline_accuracy(self, note_text: str, source_text: str) -> RuleResult:
        """检测笔记中的时序表述是否与原文一致"""
        issues = []

        # 检测时间顺序性表述
        timeline_patterns = [
            (r'(?:首先|第一步|第一阶段).*?(?:然后|接着|第二步)',
             '顺序性描述', 'first_then'),
            (r'(?:在.*之前|先.*后|前期.*后期)',
             '先后关系', 'before_after'),
            (r'(?:最初|一开始|开始时).*?(?:后来|最终|最后|结果)',
             '始末关系', 'initial_then'),
        ]

        for pattern, desc, tag in timeline_patterns:
            for match in re.finditer(pattern, note_text, re.DOTALL):
                matched_text = match.group()
                # 检查时序关键词是否在原文中也有对应
                # 提取关键时序词
                key_parts = re.findall(
                    r'(首先|然后|接着|最后|最终|之前|之后|开始|后来|前期|后期)',
                    matched_text
                )
                # 如果原文中有时序词，检查方向是否一致
                source_timeline = re.findall(
                    r'(首先|然后|接着|最后|最终|之前|之后|开始|后来|前期|后期)',
                    source_text
                )
                # 简单检查：如果笔记有时序词但原文完全没有，可能虚构了时序
                if key_parts and not source_timeline:
                    line_num = note_text[:match.start()].count('\n') + 1
                    issues.append(Issue(
                        rule_id="R10",
                        rule_name="时间线准确性",
                        severity="medium",
                        line_range=f"L{line_num}",
                        description=f"疑似虚构时序关系({desc}): '{matched_text[:80]}'",
                        suggestion="原文中未找到明确的时间顺序标记，请核实事件/步骤的先后顺序是否正确"
                    ))

        score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.2)
        return RuleResult("R10", "时间线准确性", score, len(issues) == 0, issues)

    # ----------------------------------------------------------
    # R11: 引用归属
    # ----------------------------------------------------------
    def _check_quote_attribution(self, note_text: str, source_text: str) -> RuleResult:
        """检测引用/观点归属是否正确（是否张冠李戴）"""
        issues = []

        # 提取笔记中的引用模式：人名 + 说/认为/指出/表示...
        # 人名限制：2-4 个中文字符，必须在句首/标点后/空格后（避免误匹配词组中间）
        # 排除前导连词（但/而/且/又/就/也/还/都/却）
        quote_attribution_pattern = re.compile(
            r'(?:^|[\s，。、；：！？\n>|*「」""\-\[])(?:[但而且就也还都却])?'
            r'([一-鿿]{2,4})\s*'
            r'(?:说|认为|指出|表示|提到|强调|主张|分析|解释|总结道)[：:]?\s*',
            re.MULTILINE,
        )

        # 常见非人名词（排除匹配）
        non_person_words = {
            '原文', '笔记', '总结', '分析', '框架', '方法', '观点', '理论',
            '模型', '策略', '讲师', '老师', '嘉宾', '核心', '关键', '重要',
            '第一', '第二', '第三', '第四', '第五', '第六', '第七', '第八',
            '因此', '所以', '但是', '然而', '虽然', '如果', '因为', '通过',
            '大家', '我们', '他们', '你们', '自己', '什么', '这个', '那个',
            '一步', '句话', '方面', '层面', '角度', '维度', '阶段', '环节',
            '片子', '粉丝', '胡哨', '母', '数据', '刻意', '直接', '反复',
            # 扩充非人名词（R11 误报修复）
            '构建', '关键', '日常', '并尝试', '比喻', '这既', '这么',
            '现在', '以后', '然后', '接着', '最后', '首先', '其次',
            '所谓', '这个', '那个', '一个', '这种', '那种', '这些',
            '博弈', '缠斗', '观察', '信号', '泡沫', '解构', '逻辑',
            '当面临', '不需要', '不是', '在于', '不会',
            '有观点', '的说法', '王骁', '比喻', '比如', '其实', '所以',
            '有学者', '产出', '写出', '具体', '每周', '每月', '每天',
            '现场', '让她', '让我', '他说', '她说', '能随口', '能不',
        }

        for match in quote_attribution_pattern.finditer(note_text):
            person = match.group(1)
            line_num = note_text[:match.start()].count('\n') + 1

            # 跳过常见非人名词（精确匹配或前缀匹配）
            if person in non_person_words or any(person.startswith(w) for w in non_person_words if len(w) >= 2):
                continue
            # 去除尾部语气词/助词后再检查（如"翟东升也"→"翟东升"）
            person_stripped = re.sub(r'[也的了着过吗呢吧啊哦嘛]$', '', person)
            if person_stripped != person and person_stripped in source_text:
                continue

            # 检查这个人名是否在原文中出现过
            # 支持 ASR 常见的同音/形近字替换（如 翟→狄）
            name_in_source = person in source_text
            if not name_in_source:
                # 尝试同音/形近字模糊匹配（常见 ASR 误识别对）
                fuzzy_pairs = {
                    '翟': '狄', '狄': '翟',
                    '杨': '扬', '扬': '杨',
                    '刘': '留', '留': '刘',
                    '张': '章', '章': '张',
                }
                for src_char, tgt_char in fuzzy_pairs.items():
                    if src_char in person:
                        fuzzy_name = person.replace(src_char, tgt_char)
                        if fuzzy_name in source_text:
                            name_in_source = True
                            break

            if not name_in_source:
                issues.append(Issue(
                    rule_id="R11",
                    rule_name="引用归属",
                    severity="major",
                    line_range=f"L{line_num}",
                    description=f"引用归属存疑: '{person}'在原文中未出现，可能存在张冠李戴",
                    suggestion=f"请核实'{person}'是否为正确的发言者；如为ASR误识别，请标注"
                ))

        score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.2)
        return RuleResult("R11", "引用归属", score, len(issues) == 0, issues)

    # ----------------------------------------------------------
    # R12: 人名/数字一致性
    # ----------------------------------------------------------
    def _check_name_number_consistency(self, note_text: str,
                                        source_text: str) -> RuleResult:
        """校验笔记中的人名和关键数字是否与转写原文一致"""
        issues = []

        # 1. 人名一致性：提取笔记中的「X说/认为/指出」模式，检查原文
        name_pattern = re.compile(
            r'(?:^|[\s，。；：\n])([一-鿿]{2,4})\s*(?:说|认为|指出|表示|提到|强调|解释|分析)',
            re.MULTILINE,
        )
        non_person = {
            '讲师', '老师', '嘉宾', '主持人', '原文', '笔记', '总结',
            '分析', '框架', '方法', '观点', '策略', '模型', '理论',
            '大家', '我们', '他们', '你们', '自己', '什么', '这个',
            '那个', '王骁', '有观点', '有学者', '比喻', '说法',
            '能随口', '能不', '现场', '让她', '让我', '他说', '她说',
            '关键', '核心', '重要', '构建', '日常', '比如', '其实',
        }

        checked_names = set()
        for match in name_pattern.finditer(note_text):
            name = match.group(1)
            if name in non_person or len(name) < 2:
                continue
            if name in checked_names:
                continue
            checked_names.add(name)

            # 检查原文
            if name not in source_text:
                # 同音/形近字模糊匹配
                fuzzy_pairs = {'翟': '狄', '狄': '翟', '杨': '扬', '扬': '杨'}
                found = False
                for src_c, tgt_c in fuzzy_pairs.items():
                    if src_c in name and name.replace(src_c, tgt_c) in source_text:
                        found = True
                        break
                if not found:
                    # 去除尾部语气词再试
                    stripped = re.sub(r'[也的了着过吗呢吧啊哦嘛]$', '', name)
                    if stripped != name and stripped in source_text:
                        found = True

                if not found:
                    line_num = note_text[:match.start()].count('\n') + 1
                    issues.append(Issue(
                        rule_id="R12",
                        rule_name="人名/数字一致性",
                        severity="medium",
                        line_range=f"L{line_num}",
                        description=f"人名'{name}'在转写原文中未找到对应，可能为误写或ASR差异",
                        suggestion=f"请核实'{name}'是否正确，或标注「原文此处不清晰」"
                    ))

        # 2. 关键数字一致性：提取笔记中的百分比和大数字，检查原文
        number_pattern = re.compile(r'[\d.]+\s*%')
        for match in number_pattern.finditer(note_text):
            num = match.group()
            # 使用智能匹配（支持 "百分之X"、中文数字、近似表达等）
            if not self._number_in_source(num, source_text):
                line_num = note_text[:match.start()].count('\n') + 1
                issues.append(Issue(
                    rule_id="R12",
                    rule_name="人名/数字一致性",
                    severity="medium",
                    line_range=f"L{line_num}",
                    description=f"数字'{num}'在转写原文中未找到对应",
                    suggestion="请核实该数字是否有原文出处，或改为定性描述"
                ))

        score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.15)
        return RuleResult("R12", "人名/数字一致性", score, len(issues) == 0, issues)

    # ----------------------------------------------------------
    # 辅助方法
    # ----------------------------------------------------------

    # 数字 → 中文映射（0-100 + 整十）
    _DIGIT_TO_CN = {
        '0': '零', '1': '一', '2': '二', '3': '三', '4': '四',
        '5': '五', '6': '六', '7': '七', '8': '八', '9': '九',
        '10': '十', '20': '二十', '30': '三十', '40': '四十',
        '50': '五十', '60': '六十', '70': '七十', '80': '八十',
        '90': '九十', '100': '一百',
    }

    @classmethod
    def _num_to_chinese(cls, num_str: str) -> str:
        """将数字字符串转为中文（支持 0-999 的整数和一位小数）"""
        try:
            val = float(num_str)
        except ValueError:
            return ""
        # 整数
        if val == int(val):
            n = int(val)
            if 0 <= n <= 100:
                if n <= 10:
                    return cls._DIGIT_TO_CN.get(str(n), '')
                if n % 10 == 0:
                    return cls._DIGIT_TO_CN.get(str(n), '')
                tens, ones = divmod(n, 10)
                t = cls._DIGIT_TO_CN.get(str(tens * 10), '')
                o = cls._DIGIT_TO_CN.get(str(ones), '')
                return t + o
            return str(n)  # 超过100不转换
        # 一位小数
        parts = num_str.split('.')
        if len(parts) == 2 and len(parts[1]) <= 1:
            int_part = cls._num_to_chinese(parts[0])
            dec_part = cls._DIGIT_TO_CN.get(parts[1], '')
            return f"{int_part}点{dec_part}" if int_part else ""
        return ""

    @classmethod
    def _number_in_source(cls, num_expr: str, source_text: str) -> bool:
        """智能数字匹配：检查数字表达式是否在原文中有对应

        支持的匹配模式：
        - 精确匹配: "25%" in source
        - 去空格: "25%" vs "25 %"
        - 百分之X: "25%" vs "百分之二十五"
        - 近似前缀: "约25%" vs "25%"
        - 纯数字: "25" in "百分之二十五"
        - 中文数字: "25%" vs "二十五个百分点"
        """
        # 1. 精确匹配
        if num_expr in source_text:
            return True

        # 提取数值部分
        num_match = re.search(r'[\d.]+', num_expr)
        if not num_match:
            return False
        num_val = num_match.group()

        # 2. 去空格变体（"25 %" ↔ "25%"）
        no_space = num_val + '%'
        if no_space in source_text:
            return True
        with_space = num_val + ' %'
        if with_space in source_text:
            return True

        # 3. "百分之X" 格式
        cn_num = cls._num_to_chinese(num_val)
        if cn_num:
            pct_cn = f"百分之{cn_num}"
            if pct_cn in source_text:
                return True
            # 4. "X个百分点" / "X个点"
            if f"{cn_num}个百分点" in source_text:
                return True
            if f"{cn_num}个点" in source_text:
                return True
            # 5. 纯中文数字（原文可能没有"百分之"）
            if cn_num in source_text:
                return True

        # 6. 去除近似前缀后匹配
        stripped = re.sub(r'^[约近超大约不到接近]+', '', num_expr)
        if stripped != num_expr and stripped in source_text:
            return True

        # 7. 纯数字在原文中出现
        if num_val in source_text:
            return True

        return False

    @staticmethod
    def _read_file(path: str) -> str:
        """读取文件内容（尝试 UTF-8，回退 GBK）"""
        for encoding in ('utf-8', 'gbk', 'gb2312'):
            try:
                with open(path, 'r', encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法读取文件（编码问题）: {path}")


def generate_markdown_report(report: QualityReport) -> str:
    """生成Markdown格式的质量报告"""
    lines = [
        "# 📊 笔记质量评估报告",
        "",
        f"**笔记文件**: `{report.note_path}`",
        f"**原文文件**: `{report.source_path}`",
        f"**综合评分**: **{report.total_score:.2%}** {'✅ 通过' if report.overall_passed else '❌ 未通过'}",
        "",
        report.summary,
        "",
        "---",
        "",
        "## 逐项评分",
        "",
        "| 规则 | 得分 | 状态 | 问题数 |",
        "|------|------|------|--------|",
    ]

    for rid in ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12"]:
        if rid in report.rule_results:
            rr = report.rule_results[rid]
            status = "✅" if rr.passed else "❌"
            lines.append(
                f"| {rr.rule_name} | {rr.score:.0%} | {status} | {len(rr.issues)} |"
            )

    lines.append("")
    lines.append("---")

    # 详细问题清单
    all_issues = []
    for rid in ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12"]:
        if rid in report.rule_results:
            all_issues.extend(report.rule_results[rid].issues)

    if all_issues:
        lines.append("")
        lines.append("## ⚠️ 问题清单")
        lines.append("")
        for i, issue in enumerate(all_issues, 1):
            lines.append(f"### {i}. [{issue.severity.upper()}] {issue.rule_name}")
            lines.append(f"- **位置**: {issue.line_range}")
            lines.append(f"- **描述**: {issue.description}")
            lines.append(f"- **建议**: {issue.suggestion}")
            lines.append("")
    else:
        lines.append("")
        lines.append("## ✅ 无问题发现")
        lines.append("")
        lines.append("所有检查均通过，笔记质量合格。")

    # 启发式质量指标
    if report.metrics:
        m = report.metrics
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 📈 内容质量指标")
        lines.append("")
        lines.append("| 指标 | 值 | 说明 |")
        lines.append("|------|-----|------|")
        lines.append(f"| 压缩比 | {m.compression_ratio:.1%} | 笔记/原文字数比（理想 10-30%） |")
        lines.append(f"| 结构丰富度 | {m.structure_score:.0%} | 标题+列表+表格+引用 |")
        lines.append(f"| 信息密度 | {m.info_density:.0%} | 概念多样性/句数 |")
        lines.append(f"| 可读性 | {m.readability_score:.0%} | 段落长度+列表密度 |")
        lines.append(f"| 原话引用比 | {m.quote_ratio:.1%} | 引用句/总行数 |")
        lines.append(f"| 行动具体性 | {m.action_specificity:.0%} | 可执行行动项/总行动项 |")
        lines.append(f"| **综合丰富度** | **{m.overall_richness:.0%}** | 加权平均 |")

    # LLM 评审结果
    if report.llm_eval:
        llm = report.llm_eval
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 🤖 LLM 深度评审")
        lines.append("")
        lines.append("| 维度 | 评分 |")
        lines.append("|------|------|")
        lines.append(f"| 内容丰富度 | {llm.richness_score:.1f}/5 |")
        lines.append(f"| 可读性 | {llm.readability_score:.1f}/5 |")
        lines.append(f"| 忠实度 | {llm.faithfulness_score:.1f}/5 |")
        lines.append(f"| 可行动性 | {llm.actionability_score:.1f}/5 |")
        lines.append(f"| **综合** | **{llm.overall_score:.1f}/5** |")
        if llm.feedback:
            lines.append("")
            lines.append(f"**总评**: {llm.feedback}")
        if llm.suggestions:
            lines.append("")
            lines.append("**改进建议**:")
            for s in llm.suggestions:
                lines.append(f"- {s}")

    lines.append("---")
    lines.append(f"*报告生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append(f"*校验引擎: QualityGate v2.1*")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="笔记质量自动评分脚本 - 基于6维规则检查笔记生成质量"
    )
    parser.add_argument("note", help="笔记文件路径 (.md)")
    parser.add_argument("source", help="原文转写文件路径 (.txt 或 .md)")
    parser.add_argument(
        "-o", "--output",
        help="输出报告路径 (默认输出到笔记同目录下的 quality_report.md)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="详细输出模式"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出JSON格式"
    )
    parser.add_argument(
        "--rules",
        help="规则配置文件路径 (note_generation_rules.yaml，用于加载 KEY_CONCEPTS)"
    )
    parser.add_argument(
        "--content-type",
        choices=["lecture", "tutorial", "interview", "podcast", "meeting"],
        help="内容类型（决定 R4 加载哪个领域的概念）"
    )
    parser.add_argument(
        "--llm-eval",
        action="store_true",
        help="启用 LLM 深度评审（需要 API 可用）"
    )

    args = parser.parse_args()

    if not os.path.exists(args.note):
        print(f"[ERROR] 笔记文件不存在: {args.note}")
        sys.exit(1)
    if not os.path.exists(args.source):
        print(f"[ERROR] 原文文件不存在: {args.source}")
        sys.exit(1)

    gate = QualityGate(rules_path=args.rules, content_type=args.content_type)
    report = gate.evaluate(args.note, args.source)

    # 可选：LLM 深度评审
    if args.llm_eval:
        try:
            from llm_providers import create_provider
            import yaml
            config_path = os.path.join(os.path.dirname(args.note) or '.', '..',
                                        'config', 'llm_engine_config.yaml')
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                provider = create_provider(config.get('provider', {}))
                note_text = gate._read_file(args.note)
                source_text = gate._read_file(args.source)
                llm_result = gate.llm_evaluate(note_text, source_text, provider)
                if llm_result:
                    report.llm_eval = llm_result
                    print(f"[INFO] LLM 评审完成: {llm_result.overall_score:.1f}/5.0")
        except Exception as e:
            print(f"[WARN] LLM 评审跳过: {e}")

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        md_report = generate_markdown_report(report)
        print(md_report)

        if args.output:
            output_path = args.output
        else:
            note_dir = os.path.dirname(args.note) or "."
            output_path = os.path.join(note_dir, "quality_report.md")

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(md_report)
        print(f"\n[INFO] 报告已保存: {output_path}")

    if args.verbose:
        print(f"\n[DEBUG] 评分详情:")
        for rid, rr in report.rule_results.items():
            print(f"  {rr.rule_name}: {rr.score:.2f} ({len(rr.issues)} issues)")

    sys.exit(0 if report.overall_passed else 1)


if __name__ == "__main__":
    main()
