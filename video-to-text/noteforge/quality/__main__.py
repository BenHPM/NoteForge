# -*- coding: utf-8 -*-
"""
noteforge.quality CLI — 质量引擎快速评测入口

用法:
    python -m noteforge.quality note.md source.txt                # 文件模式
    python -m noteforge.quality --text "note" --source-text "text"  # 文本模式
    python -m noteforge.quality --rule R4 --text "note" --source-text "text"  # 单规则调试
    python -m noteforge.quality --rule R4 --text "note" --source-text "text" --content-type lecture  # 带内容类型
    python -m noteforge.quality --llm-eval note.md source.txt     # 含 LLM 深度评审

不需要触发完整 pipeline，直接给质量引擎喂文本。
"""
import os
import sys
import json
import argparse
import logging
from noteforge.quality.gate import QualityGate
from noteforge.quality.models import QualityReport, RuleResult
from noteforge.quality.report import generate_markdown_report


def _load_text(args) -> tuple:
    """根据参数加载笔记和原文文本"""
    if args.text is not None and args.source_text is not None:
        return args.text, args.source_text, "<stdin>", "<stdin>"

    if args.note and args.source:
        if not os.path.exists(args.note):
            print(f"[ERROR] 笔记文件不存在: {args.note}", file=sys.stderr)
            sys.exit(1)
        if not os.path.exists(args.source):
            print(f"[ERROR] 原文文件不存在: {args.source}", file=sys.stderr)
            sys.exit(1)
        from noteforge.infra.file_io import read_file
        return read_file(args.note), read_file(args.source), args.note, args.source

    print("[ERROR] 需要 --text+--source-text 或 note+source 文件路径", file=sys.stderr)
    sys.exit(1)


def _print_rule_result(result: RuleResult, rule_id: str):
    """打印单条规则的详细结果"""
    status = "✅ PASS" if result.passed else "❌ FAIL"
    print(f"\n{'='*60}")
    print(f"  {status}  {rule_id}: {result.rule_name}")
    print(f"  得分: {result.score:.0%}  |  问题数: {len(result.issues)}")
    print(f"{'='*60}")

    if result.issues:
        for i, issue in enumerate(result.issues, 1):
            print(f"\n  [{i}] [{issue.severity.upper()}] {issue.line_range}")
            print(f"      描述: {issue.description}")
            print(f"      建议: {issue.suggestion}")
    else:
        print("\n  (无问题)")


def _build_gate(args) -> QualityGate:
    """根据参数构造 QualityGate"""
    rules_path = getattr(args, 'rules', None)
    return QualityGate(
        rules_path=rules_path,
        content_type=getattr(args, 'content_type', None),
    )


def main():
    parser = argparse.ArgumentParser(
        description="NoteForge 质量引擎评测入口 — 快速验证规则迭代",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s note.md transcript.txt                          # 完整质量评估（文件模式）
  %(prog)s --text "笔记内容" --source-text "原文内容"       # 完整质量评估（文本模式）
  %(prog)s --rule R4 --text "笔记" --source-text "原文"     # 单规则调试
  %(prog)s --rule R4 --text "笔记" --source-text "原文" --content-type lecture
  %(prog)s note.md transcript.txt --llm-eval               # 含 LLM 深度评审
  %(prog)s --rule R1,R4,R8 --text "笔记" --source-text "原文"  # 多规则批量调试
        """,
    )

    # 输入
    input_group = parser.add_argument_group('输入')
    input_group.add_argument("note", nargs="?", default=None,
                             help="笔记文件路径 (.md)")
    input_group.add_argument("source", nargs="?", default=None,
                             help="转写文件路径 (.txt)")
    input_group.add_argument("--text", default=None,
                             help="笔记文本（直接传入，不读文件）")
    input_group.add_argument("--source-text", default=None,
                             help="原文文本（直接传入，不读文件，--text 的搭档）")

    # 规则调试
    debug_group = parser.add_argument_group('规则调试')
    debug_group.add_argument("--rule", default=None, dest="rule_id",
                             help="单规则调试：指定规则 ID（如 R4, R8）。可逗号分隔多规则")

    # 配置
    config_group = parser.add_argument_group('配置')
    config_group.add_argument("--content-type",
                              choices=["lecture", "tutorial", "interview", "podcast", "meeting"],
                              help="内容类型（影响 R4 概念检查领域）")
    config_group.add_argument("--rules",
                              help="规则配置路径 (note_generation_rules.yaml)")

    # 输出
    output_group = parser.add_argument_group('输出')
    output_group.add_argument("--json", action="store_true",
                              help="输出 JSON 格式")
    output_group.add_argument("--verbose", "-v", action="store_true",
                              help="详细模式（显示规则执行详情）")
    output_group.add_argument("--llm-eval", action="store_true",
                              help="启用 LLM 深度评审（需要 API 可用）")
    output_group.add_argument("--quiet", "-q", action="store_true",
                              help="仅输出分数，不打印详细报告")

    args = parser.parse_args()

    # 设置日志
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format='%(message)s')

    # 加载文本
    note_text, source_text, note_label, source_label = _load_text(args)

    gate = _build_gate(args)

    # 单规则调试模式
    if args.rule_id:
        rule_ids = [r.strip() for r in args.rule_id.split(',')]
        results = {}
        for rid in rule_ids:
            results[rid] = gate.evaluate_rule(rid, note_text, source_text,
                                               content_type=args.content_type,
                                               rules_path=args.rules)

        if args.json:
            output = {
                rid: {
                    "rule_id": rr.rule_id,
                    "rule_name": rr.rule_name,
                    "score": round(rr.score, 2),
                    "passed": rr.passed,
                    "issues": [
                        {
                            "severity": iss.severity,
                            "line_range": iss.line_range,
                            "description": iss.description,
                            "suggestion": iss.suggestion,
                        }
                        for iss in rr.issues
                    ],
                }
                for rid, rr in results.items()
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            for rid, rr in results.items():
                _print_rule_result(rr, rid)
            print()

        # 退出码：任一规则不通过则返回 1
        sys.exit(0 if all(r.passed for r in results.values()) else 1)

    # 完整质量评估
    report = gate.evaluate_text(note_text, source_text,
                                note_label=note_label,
                                source_label=source_label)

    # 可选：LLM 深度评审
    if args.llm_eval:
        try:
            from noteforge.core.llm_providers import create_provider
            from noteforge.config import load_yaml
            config_dir = os.path.dirname(note_label) or '.'
            config_path = os.path.join(config_dir, '..', 'config', 'llm_engine_config.yaml')
            config = load_yaml(config_path)
            provider = create_provider(config.get('provider', {}))
            llm_result = gate.llm_evaluate(note_text, source_text, provider)
            if llm_result:
                report.llm_eval = llm_result
                print(f"[INFO] LLM 评审完成: {llm_result.overall_score:.1f}/5.0")
        except Exception as e:
            print(f"[WARN] LLM 评审跳过: {e}", file=sys.stderr)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    elif args.quiet:
        passed = "PASS" if report.overall_passed else "FAIL"
        print(f"{report.total_score:.2%} [{passed}]")
    else:
        md = generate_markdown_report(report)
        print(md)

    if args.verbose:
        logger = logging.getLogger('noteforge.quality')
        logger.debug("评分详情:")
        for rid, rr in report.rule_results.items():
            logger.debug(f"  {rr.rule_name}: {rr.score:.2f} ({len(rr.issues)} issues)")

    sys.exit(0 if report.overall_passed else 1)


if __name__ == '__main__':
    main()
