# -*- coding: utf-8 -*-
"""
NoteForge 笔记版本对比测试工具
用途: 对同一转写文本的多个笔记版本做质量评分对比
用法: python -m noteforge.cli.compare <source_transcript> <note_v1> <note_v2> [note_v3 ...]
"""

import os
import sys
import json
import argparse
from pathlib import Path

from noteforge.quality.gate import QualityGate


def compare_notes(source_path: str, note_paths: list,
                  rules_path: str = None, content_type: str = None):
    """
    对比多个笔记版本的质量

    Args:
        source_path: 转写原文路径
        note_paths: 笔记文件路径列表
        rules_path: 规则配置文件路径
        content_type: 内容类型
    """
    gate = QualityGate(rules_path=rules_path, content_type=content_type)

    results = []
    for note_path in note_paths:
        if not os.path.exists(note_path):
            print(f"[WARN] 文件不存在，跳过: {note_path}")
            continue

        report = gate.evaluate(note_path, source_path)
        name = Path(note_path).stem

        # 统计
        total_issues = sum(len(rr.issues) for rr in report.rule_results.values())
        fatal_issues = sum(
            1 for rr in report.rule_results.values()
            for iss in rr.issues if iss.severity == 'fatal'
        )
        major_issues = sum(
            1 for rr in report.rule_results.values()
            for iss in rr.issues if iss.severity == 'major'
        )

        # 文件大小
        file_size = os.path.getsize(note_path)
        with open(note_path, encoding='utf-8') as f:
            line_count = len(f.readlines())

        results.append({
            'name': name,
            'path': note_path,
            'score': report.total_score,
            'passed': report.overall_passed,
            'total_issues': total_issues,
            'fatal_issues': fatal_issues,
            'major_issues': major_issues,
            'line_count': line_count,
            'file_size': file_size,
            'report': report,
        })

    if not results:
        print("[ERROR] 没有有效的笔记文件可比较")
        return

    # 排序：按分数降序
    results.sort(key=lambda r: r['score'], reverse=True)

    # 输出对比表
    print("=" * 80)
    print("  NoteForge 笔记质量对比报告")
    print(f"  转写原文: {Path(source_path).name}")
    print(f"  内容类型: {content_type or 'auto'}")
    print("=" * 80)
    print()

    # 总分对比
    print(f"{'版本':<25} {'总分':>8} {'问题':>6} {'致命':>6} {'行数':>8} {'大小':>10}")
    print("-" * 70)
    for r in results:
        size_kb = r['file_size'] / 1024
        print(
            f"{r['name']:<25} "
            f"{r['score']:>7.1%} "
            f"{r['total_issues']:>6} "
            f"{r['fatal_issues']:>6} "
            f"{r['line_count']:>8} "
            f"{size_kb:>8.1f}KB"
        )
    print()

    # 逐规则对比
    rule_ids = ['R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9', 'R10', 'R11', 'R12']
    rule_names = {}
    if results:
        for rid in rule_ids:
            rr = results[0]['report'].rule_results.get(rid)
            if rr:
                rule_names[rid] = rr.rule_name

    print("逐规则得分:")
    header = f"{'规则':<20}"
    for r in results:
        short_name = r['name'][:12]
        header += f" {short_name:>14}"
    print(header)
    print("-" * (20 + 15 * len(results)))

    for rid in rule_ids:
        line = f"{rid} {rule_names.get(rid, ''):<16}"
        for r in results:
            rr = r['report'].rule_results.get(rid)
            if rr:
                if len(rr.issues) > 0:
                    line += f" {rr.score:>10.0%}({len(rr.issues):>2})"
                else:
                    line += f" {rr.score:>14.0%}"
            else:
                line += f" {'N/A':>14}"
        print(line)
    print()

    # 问题详情（仅输出有差异的规则）
    print("问题详情:")
    for rid in rule_ids:
        has_issues = any(
            len(r['report'].rule_results.get(rid, type('', (), {'issues': []})()).issues) > 0
            for r in results
        )
        if not has_issues:
            continue

        print(f"\n  [{rid}] {rule_names.get(rid, '')}:")
        for r in results:
            rr = r['report'].rule_results.get(rid)
            if rr and rr.issues:
                print(f"    {r['name']}:")
                for iss in rr.issues:
                    print(f"      [{iss.severity}] {iss.description[:80]}")

    print()
    print("=" * 80)

    # 返回结构化结果
    return results


def main():
    parser = argparse.ArgumentParser(
        description="NoteForge 笔记版本对比测试工具"
    )
    parser.add_argument("source", help="转写原文路径 (.txt)")
    parser.add_argument("notes", nargs="+", help="笔记文件路径（2个以上）")
    parser.add_argument("--rules", help="规则配置文件路径")
    parser.add_argument(
        "--content-type",
        choices=["lecture", "tutorial", "interview", "podcast", "meeting"],
        help="内容类型"
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    args = parser.parse_args()

    if not os.path.exists(args.source):
        print(f"[ERROR] 转写文件不存在: {args.source}")
        sys.exit(1)

    results = compare_notes(
        args.source, args.notes,
        rules_path=args.rules,
        content_type=args.content_type,
    )

    if args.json and results:
        output = []
        for r in results:
            output.append({
                'name': r['name'],
                'score': round(r['score'], 4),
                'passed': r['passed'],
                'total_issues': r['total_issues'],
                'line_count': r['line_count'],
                'file_size': r['file_size'],
            })
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
