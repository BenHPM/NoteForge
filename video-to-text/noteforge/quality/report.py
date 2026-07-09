# -*- coding: utf-8 -*-
"""
NoteForge 质量报告生成 + CLI 入口

提取自 quality/gate.py 的 generate_markdown_report 函数和 main() CLI。
"""

import os
import sys
import json
import logging
import argparse

from noteforge.quality.models import QualityReport
from noteforge.quality.gate import QualityGate
from noteforge.infra.file_io import read_file

logger = logging.getLogger('noteforge.quality')


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
            from noteforge.core.llm_providers import create_provider
            from noteforge.config import load_yaml
            config_path = os.path.join(os.path.dirname(args.note) or '..',
                                        'config', 'llm_engine_config.yaml')
            config = load_yaml(config_path)
            provider = create_provider(config.get('provider', {}))
            note_text = read_file(args.note)
            source_text = read_file(args.source)
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
        import logging
        logger = logging.getLogger(__name__)
        logger.debug("评分详情:")
        for rid, rr in report.rule_results.items():
            logger.debug(f"  {rr.rule_name}: {rr.score:.2f} ({len(rr.issues)} issues)")

    sys.exit(0 if report.overall_passed else 1)
