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
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional


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
class QualityReport:
    """完整质量评估报告"""
    note_path: str
    source_path: str
    total_score: float
    rule_results: Dict[str, RuleResult]
    overall_passed: bool
    summary: str

    def to_dict(self):
        return {
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


class QualityGate:
    """笔记质量评分引擎"""

    # 规则权重配置
    RULE_WEIGHTS = {
        "R1": 25,   # 禁止虚构数据
        "R2": 20,   # 禁止越界增补
        "R3": 25,   # 禁止事实反转
        "R4": 15,   # 禁止概念失真
        "R5": 10,   # 覆盖度底线
        "R6": 5,    # 术语一致性
    }

    # 金融投资领域易被编造的模式
    FABRICATED_PATTERNS = [
        r'占比\s*[约达]?\s*[\d.]+%',           # 占比XX%
        r'权重\s*[约达]?\s*[\d.]+%',            # 权重XX%
        r'贡献\s*[约达]?\s*[\d.]+%',            # 贡献XX%
        r'[约近超]?\s*[\d.]+%\s*[以之]*[外来]',  # XX%来自
        r'[约近]\s*[\d.]+倍',                   # 约X倍
        r'占比.*?(\d+[./]\d+)',                 # 占比几分之几
    ]

    # 需保留关键限定词的专业概念（概念名 -> 必须包含的关键词列表）
    KEY_CONCEPTS = {
        "T0策略": ["中低频", "长周期预测", "高抛低吸", "自动化"],
        "指增策略": ["超额收益", "宽基指数", "成分股"],
        "周期投资": ["供给端", "资本开支", "ROE", "基本面趋势"],
        "非可解释性因子": ["不可解释", "非线性组合", "模型训练"],
        "高频策略": ["毫秒级", "盘口", "竞争"],
        "价值投资": ["内在价值", "长期持有", "定价"],
    }

    def evaluate(self, note_path: str, source_path: str) -> QualityReport:
        """评估笔记质量"""
        note_text = self._read_file(note_path)
        source_text = self._read_file(source_path)

        results = {}
        results["R1"] = self._check_fabricated_data(note_text, source_text)
        results["R2"] = self._check_unmarked_additions(note_text, source_text)
        results["R3"] = self._check_semantic_reversal(note_text, source_text)
        results["R4"] = self._check_concept_distortion(note_text)
        results["R5"] = self._check_coverage(note_text, source_text)
        results["R6"] = self._check_consistency(note_text)

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
            for rid in ["R1", "R2", "R3"]
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

        return QualityReport(
            note_path=note_path,
            source_path=source_path,
            total_score=total_score,
            rule_results=results,
            overall_passed=overall_passed,
            summary=summary
        )

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

                # 在原文中查找该数字
                if number_context not in source_text:
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

        # 检测可能有增补嫌疑的段落（带建议语气的模式）
        suspicion_patterns = [
            (r'✅\s*短期应对.*?(?=\n\n|\Z)', '策略建议类内容'),
            (r'📈\s*中期布局.*?(?=\n\n|\Z)', '策略建议类内容'),
            (r'应对策略建议', '策略建议标题'),
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

        for concept, required_keywords in self.KEY_CONCEPTS.items():
            if concept in note_text:
                # 找到概念出现的上下文（前后各200字符）
                for match in re.finditer(re.escape(concept), note_text):
                    start = max(0, match.start() - 200)
                    end = min(len(note_text), match.end() + 200)
                    context = note_text[start:end]

                    # 检查必有关键词
                    missing = [
                        kw for kw in required_keywords
                        if kw not in context
                    ]

                    if missing:
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
            # 通用标题格式
            r'^#{1,3}\s+(.+)$',
        ]

        source_chapters = []
        for pattern in chapter_patterns:
            source_chapters.extend(re.findall(pattern, source_text, re.MULTILINE))

        # 去重
        source_chapters = list(dict.fromkeys(source_chapters))

        if not source_chapters:
            # 如果没找到章节标记，按主题关键词检测
            topic_keywords = [
                "碳酸锂", "铜", "铝", "黄金", "白银", "稀土", "煤炭", "石油",
                "霍尔木兹", "资本开支", "供给", "需求", "ROE",
                "周期投资", "价值投资", "T0策略", "指增策略", "高频策略",
                "因子", "AI", "算力", "人才",
            ]
            covered = sum(1 for kw in topic_keywords if kw in note_text)
            total = len(topic_keywords)
            ratio = covered / total if total > 0 else 1.0
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

        if ratio < 0.80:
            issues.append(Issue(
                rule_id="R5",
                rule_name="覆盖度底线",
                severity="major",
                line_range="全文",
                description=f"笔记覆盖率为 {ratio:.1%}，低于80%底线。原文约{len(source_chapters) if source_chapters else 'N/A'}个议题，笔记仅覆盖约{int(ratio * (len(source_chapters) or len(topic_keywords)))}个",
                suggestion="请对照原文章节列表检查遗漏的议题并补充"
            ))

        return RuleResult(
            "R5", "覆盖度底线",
            min(1.0, ratio / 0.80),
            ratio >= 0.80,
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
    # 辅助方法
    # ----------------------------------------------------------
    @staticmethod
    def _read_file(path: str) -> str:
        """读取文件内容"""
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()


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

    for rid in ["R1", "R2", "R3", "R4", "R5", "R6"]:
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
    for rid in ["R1", "R2", "R3", "R4", "R5", "R6"]:
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
        lines.append("所有6项检查均通过，笔记质量合格。")

    lines.append("---")
    lines.append(f"*报告生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append(f"*校验引擎: QualityGate v1.0*")

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

    args = parser.parse_args()

    if not os.path.exists(args.note):
        print(f"[ERROR] 笔记文件不存在: {args.note}")
        sys.exit(1)
    if not os.path.exists(args.source):
        print(f"[ERROR] 原文文件不存在: {args.source}")
        sys.exit(1)

    gate = QualityGate()
    report = gate.evaluate(args.note, args.source)

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
