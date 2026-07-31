# -*- coding: utf-8 -*-
"""质量检查命令（支持 --format json|md|table 和 --verbose）"""
import os
import json

from noteforge.quality.report import generate_markdown_report
from noteforge.quality.models import Issue, RuleResult, QualityReport, LLMEvalResult


# 规则 ID → 中文名映射
_RULE_NAMES = {
    "R0": "内容完整性",
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


def _report_to_quality_report(data: dict) -> QualityReport:
    """将 JSON dict 还原为 QualityReport 对象（用于 generate_markdown_report）"""
    rule_results = {}
    for rid, rr_data in data.get('rule_results', {}).items():
        issues = [
            Issue(
                rule_id=rid,
                rule_name=_RULE_NAMES.get(rid, rid),
                severity=iss.get('severity', 'medium'),
                line_range=iss.get('line_range', ''),
                description=iss.get('description', ''),
                suggestion=iss.get('suggestion', ''),
            )
            for iss in rr_data.get('issues', [])
        ]
        rule_results[rid] = RuleResult(
            rule_id=rid,
            rule_name=_RULE_NAMES.get(rid, rid),
            score=rr_data.get('score', 0),
            passed=rr_data.get('passed', False),
            issues=issues,
        )

    llm_eval = None
    if data.get('llm_eval'):
        le = data['llm_eval']
        llm_eval = LLMEvalResult(
            richness_score=le.get('richness_score', 0),
            readability_score=le.get('readability_score', 0),
            faithfulness_score=le.get('faithfulness_score', 0),
            actionability_score=le.get('actionability_score', 0),
            overall_score=le.get('overall_score', 0),
            feedback=le.get('feedback', ''),
            suggestions=le.get('suggestions', []),
        )

    return QualityReport(
        note_path=data.get('note_path'),
        source_path=data.get('source_path'),
        note_label=data.get('note_label', '<note>'),
        source_label=data.get('source_label', '<source>'),
        total_score=data.get('total_score', 0),
        overall_passed=data.get('overall_passed', False),
        summary=data.get('summary', ''),
        rule_results=rule_results,
        llm_eval=llm_eval,
    )


def _format_table(report: dict, verbose: bool = False) -> str:
    """将报告格式化为表格文本"""
    lines = []
    total = report.get('total_score', 0)
    passed = report.get('overall_passed', False)
    status = 'PASS' if passed else 'FAIL'

    lines.append("=" * 70)
    lines.append("  Quality Report")
    lines.append("=" * 70)
    lines.append(f"  Score: {total:.0%}  |  Status: {status}")
    lines.append("")

    # 规则结果表
    header = f"  {'Rule':<5} {'Name':<16} {'Score':>6} {'Status':>6} {'Issues':>7}"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    for rid in ['R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7',
                'R8', 'R9', 'R10', 'R11', 'R12']:
        rr = report.get('rule_results', {}).get(rid)
        if rr is None:
            continue
        score = rr.get('score', 0)
        ok = 'PASS' if rr.get('passed', False) else 'FAIL'
        issue_count = rr.get('issue_count', len(rr.get('issues', [])))
        name = _RULE_NAMES.get(rid, rid)
        lines.append(f"  {rid:<5} {name:<16} {score:>5.0%} {ok:>6} {issue_count:>7}")

    lines.append("=" * 70)

    # verbose: 显示每个规则的问题详情
    if verbose:
        for rid in ['R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7',
                    'R8', 'R9', 'R10', 'R11', 'R12']:
            rr = report.get('rule_results', {}).get(rid)
            if rr is None:
                continue
            issues = rr.get('issues', [])
            if not issues:
                continue
            name = _RULE_NAMES.get(rid, rid)
            lines.append("")
            lines.append(f"  [{rid}] {name} - {len(issues)} issue(s):")
            for i, iss in enumerate(issues[:10]):
                sev = iss.get('severity', '?').upper()
                loc = iss.get('line_range', '')
                desc = iss.get('description', '')
                lines.append(f"    {i+1}. [{sev}] {loc}: {desc}")
            if len(issues) > 10:
                lines.append(f"    ... and {len(issues) - 10} more")
        lines.append("")

    # 启发式指标
    metrics = report.get('metrics')
    if metrics:
        lines.append("")
        lines.append("  Heuristic Metrics:")
        for key, label in [
            ('compression_ratio', 'Compression Ratio'),
            ('structure_score', 'Structure Richness'),
            ('info_density', 'Info Density'),
            ('readability_score', 'Readability'),
            ('quote_ratio', 'Quote Ratio'),
            ('action_specificity', 'Action Specificity'),
            ('overall_richness', 'Overall Richness'),
        ]:
            val = metrics.get(key)
            if val is not None:
                lines.append(f"    {label:<22} {val:>6.0%}")

    # LLM 评审
    llm_eval = report.get('llm_eval')
    if llm_eval:
        lines.append("")
        lines.append("  LLM Evaluation:")
        for key, label in [
            ('richness_score', 'Richness'),
            ('readability_score', 'Readability'),
            ('faithfulness_score', 'Faithfulness'),
            ('actionability_score', 'Actionability'),
            ('overall_score', 'Overall'),
        ]:
            val = llm_eval.get(key)
            if val is not None:
                lines.append(f"    {label:<22} {val:>4.1f}/5")

    return "\n".join(lines)


def run_check_only(engine, args):
    """仅质量检查模式（支持 --format json|md|table 和 --verbose）

    --format table (默认): 格式化表格输出
    --format json: 输出原始 JSON
    --format md: 输出 Markdown 报告
    --verbose: 在 table 模式下显示每条规则的详细问题
    """
    if not os.path.exists(args.check_only):
        print(f"[ERROR] 笔记文件不存在: {args.check_only}")
        return 1

    fmt = getattr(args, 'format', 'table') or 'table'
    verbose = getattr(args, 'verbose', False)

    # 对于 json/md 格式，直接调用 run_quality_gate 避免引擎内部打印旧格式
    # 对于 table 格式，使用引擎的 check_only（内部会打印旧格式报告）
    if fmt in ('json', 'md'):
        # 直接调用质量门禁，跳过 print_quality_report
        transcript_path = engine._audio_handler.find_transcript_for_note(args.check_only)
        if not transcript_path:
            print("[ERROR] 质量检查失败（未找到对应转写文件）")
            return 1
        report = engine.quality_manager.run_quality_gate(args.check_only, transcript_path)
        if report is None:
            print("[ERROR] 质量检查失败")
            return 1
        # 保存报告（与 check_only 一致）
        engine.quality_manager.save_quality_report(args.check_only, report)

        if fmt == 'json':
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif fmt == 'md':
            qr = _report_to_quality_report(report)
            print(generate_markdown_report(qr))
    else:
        # table 模式: 使用引擎的 check_only（内部会打印旧格式 + 保存报告）
        report = engine.check_only(args.check_only)
        if report is None:
            print("[ERROR] 质量检查失败（未找到对应转写文件）")
            return 1

        # verbose 模式: 补充详细问题列表
        if verbose:
            print(_format_table(report, verbose=True))

    return 0 if report.get('overall_passed') else 1
