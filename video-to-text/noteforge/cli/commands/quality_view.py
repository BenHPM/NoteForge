# -*- coding: utf-8 -*-
"""质量报告查看/列表命令"""

import os
import json
from pathlib import Path

from noteforge.quality.report import generate_markdown_report
from noteforge.quality.models import Issue, RuleResult, QualityReport, LLMEvalResult


# 规则 ID → 中文名映射（用于表格显示）
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


def _find_reports_dir(args=None):
    """定位 quality_reports 目录"""
    # 1. 从 args.output_dir 推断
    if args and getattr(args, 'output_dir', None):
        candidate = Path(args.output_dir) / 'quality_reports'
        if candidate.is_dir():
            return candidate
    # 2. 默认位置: output/quality_reports
    base = Path(__file__).parent.parent.parent.parent  # cli/commands/ -> video-to-text/
    candidate = base / 'output' / 'quality_reports'
    if candidate.is_dir():
        return candidate
    # 3. cwd 下
    candidate = Path('output') / 'quality_reports'
    if candidate.is_dir():
        return candidate.resolve()
    return None


def _load_report_json(report_path: Path) -> dict:
    """加载 JSON 质量报告"""
    with open(report_path, 'r', encoding='utf-8') as f:
        return json.load(f)


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
            # 最多显示 10 条，避免刷屏
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


def _format_json(report: dict) -> str:
    """将报告格式化为 JSON"""
    return json.dumps(report, ensure_ascii=False, indent=2)


def _format_md(report: dict) -> str:
    """将报告格式化为 Markdown（复用 generate_markdown_report）"""
    qr = _report_to_quality_report(report)
    return generate_markdown_report(qr)


def run_quality_view(args) -> int:
    """查看单个笔记的质量报告

    用法: python -m noteforge --quality-view <note_file_or_report>
    """
    note_file = getattr(args, 'quality_view', None)
    if not note_file:
        print("[ERROR] 请指定笔记文件或报告文件: --quality-view <path>")
        return 1

    note_path = Path(note_file)

    # 如果直接传入的是 JSON 报告文件
    if note_path.suffix == '.json' and note_path.exists():
        report = _load_report_json(note_path)
    else:
        # 从笔记文件名推断报告路径
        reports_dir = _find_reports_dir(args)
        if reports_dir is None:
            print("[ERROR] 未找到 quality_reports 目录")
            return 1

        stem = note_path.stem
        report_path = reports_dir / f"{stem}_quality.json"
        if not report_path.exists():
            print(f"[ERROR] 未找到质量报告: {report_path}")
            print(f"  提示: 先运行 --check-only {note_file} 生成报告")
            return 1
        report = _load_report_json(report_path)

    # 格式化输出
    fmt = getattr(args, 'format', 'table') or 'table'
    verbose = getattr(args, 'verbose', False)

    if fmt == 'json':
        print(_format_json(report))
    elif fmt == 'md':
        print(_format_md(report))
    else:
        print(_format_table(report, verbose=verbose))

    return 0 if report.get('overall_passed', False) else 1


def run_quality_list(args) -> int:
    """列出所有质量报告摘要

    用法: python -m noteforge --quality-list
    """
    reports_dir = _find_reports_dir(args)
    if reports_dir is None:
        print("[INFO] 未找到 quality_reports 目录（尚无质量报告）")
        return 0

    report_files = sorted(reports_dir.glob('*_quality.json'))
    if not report_files:
        print("[INFO] 无质量报告")
        return 0

    fmt = getattr(args, 'format', 'table') or 'table'

    if fmt == 'json':
        # JSON 模式: 输出摘要数组
        summaries = []
        for rf in report_files:
            try:
                report = _load_report_json(rf)
                summaries.append({
                    'file': rf.stem.replace('_quality', ''),
                    'score': report.get('total_score', 0),
                    'passed': report.get('overall_passed', False),
                    'rule_count': len(report.get('rule_results', {})),
                    'failed_rules': [
                        rid for rid, rr in report.get('rule_results', {}).items()
                        if not rr.get('passed', False)
                    ],
                })
            except (json.JSONDecodeError, OSError) as e:
                summaries.append({
                    'file': rf.stem.replace('_quality', ''),
                    'error': str(e),
                })
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return 0

    # 表格模式
    lines = []
    lines.append("=" * 80)
    lines.append("  Quality Reports Summary")
    lines.append("=" * 80)
    lines.append(f"  Reports directory: {reports_dir}")
    lines.append(f"  Total reports: {len(report_files)}")
    lines.append("")

    header = f"  {'#':<4} {'File':<40} {'Score':>6} {'Status':>8} {'Failed':>8}"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    pass_count = 0
    fail_count = 0
    for i, rf in enumerate(report_files, 1):
        try:
            report = _load_report_json(rf)
            score = report.get('total_score', 0)
            passed = report.get('overall_passed', False)
            failed_rules = [
                rid for rid, rr in report.get('rule_results', {}).items()
                if not rr.get('passed', False)
            ]
            status = 'PASS' if passed else 'FAIL'
            name = rf.stem.replace('_quality', '')
            # 截断过长的文件名
            if len(name) > 38:
                name = name[:35] + "..."
            failed_str = ",".join(failed_rules) if failed_rules else "-"
            if len(failed_str) > 8:
                failed_str = failed_str[:6] + ".."
            lines.append(f"  {i:<4} {name:<40} {score:>5.0%} {status:>8} {failed_str:>8}")
            if passed:
                pass_count += 1
            else:
                fail_count += 1
        except (json.JSONDecodeError, OSError) as e:
            name = rf.stem.replace('_quality', '')
            if len(name) > 38:
                name = name[:35] + "..."
            lines.append(f"  {i:<4} {name:<40} {'ERROR':>6} {'---':>8} {'---':>8}")
            fail_count += 1

    lines.append("")
    lines.append(f"  Summary: {pass_count} passed, {fail_count} failed, "
                 f"{len(report_files)} total")
    if len(report_files) > 0:
        scores = []
        for rf in report_files:
            try:
                report = _load_report_json(rf)
                scores.append(report.get('total_score', 0))
            except (json.JSONDecodeError, OSError):
                pass  # 跳过损坏的报告
        if scores:
            avg_score = sum(scores) / len(scores)
            lines.append(f"  Average score: {avg_score:.0%}")
    lines.append("=" * 80)

    print("\n".join(lines))
    return 0
