# -*- coding: utf-8 -*-
"""
LLM 评审可靠性验证工具

对 N 篇文档 × M 次运行进行 LLM 评审，计算每个维度的：
- 变异系数 (CV = σ/μ)，CV < 15% 为可靠
- 分数分布 (min, max, mean, std)
- 阈值翻转次数（同输入在不同运行中 pass/fail 结果不同）

用法:
    cd video-to-text
    envs/paraformer/python.exe -m noteforge.quality.llm_eval_validator \
        --sample-size 50 --runs 3 --cost-cap 5.0

输出:
    output/logs/llm_eval_validation_{timestamp}.json — 原始数据
    控制台 — 可读性报告 + 每维度 go/no-go 判定
"""

import os
import sys
import json
import time
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict

# Windows 控制台 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

logger = logging.getLogger('noteforge.quality.llm_eval_validator')


@dataclass
class DimensionStats:
    """单维度的统计结果"""
    dimension: str
    values: List[float] = field(default_factory=list)
    mean: float = 0.0
    std: float = 0.0
    cv: float = 0.0          # 变异系数
    min_val: float = 0.0
    max_val: float = 0.0
    threshold_flips: int = 0  # pass/fail 翻转次数（以 3.0 为阈值）
    verdict: str = "pending"  # pass / fail / pending


@dataclass
class DocValidationResult:
    """单篇文档的验证结果"""
    doc_label: str
    runs: List[Dict] = field(default_factory=list)  # 每次运行的 LLMEvalResult.to_dict()
    dimension_stats: Dict[str, DimensionStats] = field(default_factory=dict)


@dataclass
class ValidationReport:
    """完整验证报告"""
    sample_size: int = 0
    runs_per_doc: int = 0
    total_llm_calls: int = 0
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    elapsed_seconds: float = 0.0
    per_doc_results: List[DocValidationResult] = field(default_factory=list)
    aggregate_stats: Dict[str, DimensionStats] = field(default_factory=dict)
    final_verdict: str = "pending"  # pass / fail / partial


def _compute_stats(values: List[float], threshold: float = 3.0) -> DimensionStats:
    """计算单维度统计量"""
    if not values:
        return DimensionStats(dimension="unknown")

    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n if n > 1 else 0.0
    std = variance ** 0.5
    cv = (std / mean * 100) if mean > 0 else float('inf')

    # 计算 pass/fail 翻转次数
    passes = [v >= threshold for v in values]
    flips = sum(1 for i in range(len(passes) - 1) if passes[i] != passes[i + 1])

    verdict = "pass" if cv < 15.0 else "fail"

    return DimensionStats(
        dimension="",
        values=values,
        mean=round(mean, 3),
        std=round(std, 3),
        cv=round(cv, 2),
        min_val=round(min(values), 2),
        max_val=round(max(values), 2),
        threshold_flips=flips,
        verdict=verdict,
    )


def run_validation(
    sample_size: int = 50,
    runs_per_doc: int = 3,
    cost_cap_usd: float = 5.0,
    notes_dir: Optional[str] = None,
    transcripts_dir: Optional[str] = None,
    config_path: Optional[str] = None,
) -> ValidationReport:
    """
    执行 LLM 评审可靠性验证

    Args:
        sample_size: 采样文档数量
        runs_per_doc: 每篇文档运行次数
        cost_cap_usd: 成本上限（美元），超过自动终止
        notes_dir: 笔记目录
        transcripts_dir: 转写目录
        config_path: llm_engine_config.yaml 路径

    Returns:
        ValidationReport
    """
    start_time = time.time()
    report = ValidationReport(
        sample_size=sample_size,
        runs_per_doc=runs_per_doc,
    )

    # --- 1. 初始化路径 ---
    base_dir = Path(__file__).parent.parent.parent / 'output'
    if notes_dir is None:
        notes_dir = str(base_dir / 'notes')
    if transcripts_dir is None:
        transcripts_dir = str(base_dir / 'transcripts')
    if config_path is None:
        config_path = str(Path(__file__).parent.parent.parent / 'config' / 'llm_engine_config.yaml')

    # --- 2. 加载配置和创建 Provider ---
    try:
        from noteforge.config import load_yaml
        from noteforge.core.llm_providers import create_provider
        config = load_yaml(config_path)
        provider = create_provider(config.get('provider', {}))
    except Exception as e:
        logger.error(f"无法初始化 LLM Provider: {e}")
        print(f"❌ 无法初始化 LLM Provider: {e}", file=sys.stderr)
        print("请确认 config/llm_engine_config.yaml 配置正确且 API Key 已设置", file=sys.stderr)
        report.final_verdict = "fail"
        return report

    # --- 3. 采集文档对 ---
    from noteforge.infra.file_io import read_file
    notes_path = Path(notes_dir)
    transcripts_path = Path(transcripts_dir)

    # 获取所有笔记（排除知识体系、矛盾分析等合成文件）
    skip_prefixes = ('knowledge_synthesis', 'mental_models', 'action_playbook',
                     'quality_report', '中国政经-知识体系', '国际分析-知识体系',
                     '地缘政治-知识体系', '地缘经济-知识体系', '短视频导演课程-知识体系',
                     '量化投资-知识体系')
    skip_suffixes = ('_contradictions.md', '_quality.json', '_quality.md')

    doc_pairs = []
    for note_file in sorted(notes_path.glob('*.md')):
        name = note_file.name
        if any(name.startswith(p) for p in skip_prefixes):
            continue
        if any(name.endswith(s) for s in skip_suffixes):
            continue

        # 查找对应转写文件
        stem = note_file.stem
        # 尝试多种匹配策略
        candidates = [
            transcripts_path / f"{stem}.txt",
        ]
        # 尝试去掉版本后缀
        for suffix in ['_v2', '_v3', '_v4', '_v5', '_2stage', '_incremental']:
            if stem.endswith(suffix):
                candidates.append(transcripts_path / f"{stem[:-len(suffix)]}.txt")

        transcript_file = None
        for c in candidates:
            if c.exists():
                transcript_file = c
                break

        if transcript_file is None:
            continue

        doc_pairs.append((note_file, transcript_file))

    if not doc_pairs:
        print("❌ 未找到任何笔记-转写文档对", file=sys.stderr)
        report.final_verdict = "fail"
        return report

    # 采样
    import random
    if len(doc_pairs) > sample_size:
        doc_pairs = random.sample(doc_pairs, sample_size)

    print(f"📊 LLM 评审可靠性验证")
    print(f"   文档数: {len(doc_pairs)} | 每文档运行: {runs_per_doc} 次")
    print(f"   成本上限: ${cost_cap_usd:.2f}")
    print(f"   预估调用: {len(doc_pairs) * runs_per_doc} 次")
    print()

    # --- 4. 执行验证 ---
    from noteforge.quality.gate import QualityGate
    gate = QualityGate()

    # Claude Sonnet 定价（美元/百万 token）
    INPUT_PRICE = 3.0
    OUTPUT_PRICE = 15.0

    for idx, (note_file, transcript_file) in enumerate(doc_pairs):
        doc_label = note_file.stem[:40]
        doc_result = DocValidationResult(doc_label=doc_label)

        try:
            note_text = read_file(str(note_file))
            source_text = read_file(str(transcript_file))
        except Exception as e:
            logger.warning(f"读取文件失败 {doc_label}: {e}")
            continue

        # 截断过长文本（避免超 token 限制）
        if len(note_text) > 8000:
            note_text = note_text[:8000]
        if len(source_text) > 6000:
            source_text = source_text[:6000]

        for run_idx in range(runs_per_doc):
            # 检查成本上限
            if report.total_cost_usd >= cost_cap_usd:
                print(f"\n⚠️ 已达成本上限 ${cost_cap_usd:.2f}，提前终止")
                break

            try:
                result = gate.llm_evaluate(note_text, source_text, provider)
                if result:
                    doc_result.runs.append(result.to_dict())
                    report.total_llm_calls += 1

                    # 累计 token 和成本
                    usage = provider.get_usage()
                    report.total_input_tokens += usage.get('input_tokens', 0)
                    report.total_output_tokens += usage.get('output_tokens', 0)
                    # 计算成本（考虑缓存）
                    cached = usage.get('cache_read_input_tokens', 0)
                    input_cost = (usage.get('input_tokens', 0) - cached) * INPUT_PRICE / 1_000_000
                    cached_cost = cached * INPUT_PRICE * 0.1 / 1_000_000  # 缓存 10%
                    output_cost = usage.get('output_tokens', 0) * OUTPUT_PRICE / 1_000_000
                    report.total_cost_usd += input_cost + cached_cost + output_cost
                else:
                    logger.warning(f"LLM 评审返回 None: {doc_label} run {run_idx + 1}")
            except Exception as e:
                logger.warning(f"LLM 评审失败: {doc_label} run {run_idx + 1}: {e}")
                time.sleep(2)  # 避免连续失败

        # 计算该文档的维度统计
        if len(doc_result.runs) >= 2:
            for dim_name, score_key in [
                ("richness", "richness_score"),
                ("readability", "readability_score"),
                ("faithfulness", "faithfulness_score"),
                ("actionability", "actionability_score"),
                ("overall", "overall_score"),
            ]:
                values = [r.get(score_key, 0) for r in doc_result.runs if score_key in r]
                if values:
                    stats = _compute_stats(values)
                    stats.dimension = dim_name
                    doc_result.dimension_stats[dim_name] = stats

        report.per_doc_results.append(doc_result)

        # 进度
        done = idx + 1
        elapsed = time.time() - start_time
        eta = elapsed / done * (len(doc_pairs) - done) if done > 0 else 0
        print(f"  [{done}/{len(doc_pairs)}] {doc_label} "
              f"(${report.total_cost_usd:.3f}) "
              f"ETA: {eta / 60:.1f}min")

        if report.total_cost_usd >= cost_cap_usd:
            break

        time.sleep(1)  # 限速

    # --- 5. 汇总统计 ---
    report.elapsed_seconds = time.time() - start_time

    # 聚合所有文档的维度值
    all_dim_values: Dict[str, List[float]] = {
        "richness": [], "readability": [], "faithfulness": [],
        "actionability": [], "overall": [],
    }
    all_dim_cvs: Dict[str, List[float]] = {k: [] for k in all_dim_values}
    total_flips = 0

    for doc in report.per_doc_results:
        for dim_name, stats in doc.dimension_stats.items():
            all_dim_values[dim_name].extend(stats.values)
            all_dim_cvs[dim_name].append(stats.cv)
            total_flips += stats.threshold_flips

    # 计算聚合统计
    for dim_name in all_dim_values:
        if all_dim_values[dim_name]:
            agg = _compute_stats(all_dim_values[dim_name])
            agg.dimension = dim_name
            # 聚合 CV：取各文档 CV 的均值
            if all_dim_cvs[dim_name]:
                agg.cv = round(sum(all_dim_cvs[dim_name]) / len(all_dim_cvs[dim_name]), 2)
                agg.verdict = "pass" if agg.cv < 15.0 else "fail"
            report.aggregate_stats[dim_name] = agg

    # 最终判定：所有维度 CV < 15% 才通过
    passing_dims = sum(1 for s in report.aggregate_stats.values() if s.verdict == "pass")
    total_dims = len(report.aggregate_stats)
    if total_dims == 0:
        report.final_verdict = "fail"
    elif passing_dims == total_dims:
        report.final_verdict = "pass"
    else:
        report.final_verdict = "partial"

    # --- 6. 保存和打印报告 ---
    _save_report(report)
    _print_report(report)

    return report


def _save_report(report: ValidationReport):
    """保存验证报告到 JSON"""
    logs_dir = Path(__file__).parent.parent.parent / 'output' / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    path = logs_dir / f'llm_eval_validation_{timestamp}.json'

    # 序列化
    data = {
        "sample_size": report.sample_size,
        "runs_per_doc": report.runs_per_doc,
        "total_llm_calls": report.total_llm_calls,
        "total_cost_usd": round(report.total_cost_usd, 4),
        "total_input_tokens": report.total_input_tokens,
        "total_output_tokens": report.total_output_tokens,
        "elapsed_seconds": round(report.elapsed_seconds, 1),
        "final_verdict": report.final_verdict,
        "aggregate_stats": {
            k: {
                "dimension": v.dimension,
                "mean": v.mean,
                "std": v.std,
                "cv": v.cv,
                "min": v.min_val,
                "max": v.max_val,
                "threshold_flips": v.threshold_flips,
                "verdict": v.verdict,
            }
            for k, v in report.aggregate_stats.items()
        },
        "per_doc_results": [
            {
                "doc_label": d.doc_label,
                "runs": d.runs,
                "dimension_stats": {
                    k: {"cv": v.cv, "verdict": v.verdict, "mean": v.mean}
                    for k, v in d.dimension_stats.items()
                },
            }
            for d in report.per_doc_results
        ],
    }

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n💾 报告已保存: {path}")


def _print_report(report: ValidationReport):
    """打印可读性验证报告"""
    print("\n" + "=" * 70)
    print("  📊 LLM 评审可靠性验证报告")
    print("=" * 70)
    print(f"  文档数: {report.sample_size} | 每文档: {report.runs_per_doc} 次")
    print(f"  LLM 调用: {report.total_llm_calls} | 成本: ${report.total_cost_usd:.4f}")
    print(f"  Token: {report.total_input_tokens:,} in + {report.total_output_tokens:,} out")
    print(f"  耗时: {report.elapsed_seconds:.0f}s")
    print()

    # 维度汇总表
    print("  ┌──────────────┬───────┬───────┬────────┬───────┬─────────┐")
    print("  │ 维度         │ 均值  │ 标准差 │ CV(%)  │ 翻转  │ 判定    │")
    print("  ├──────────────┼───────┼───────┼────────┼───────┼─────────┤")

    for dim_name in ["richness", "readability", "faithfulness", "actionability", "overall"]:
        stats = report.aggregate_stats.get(dim_name)
        if stats:
            icon = "✅" if stats.verdict == "pass" else "❌"
            print(f"  │ {dim_name:<12} │ {stats.mean:>5.2f} │ {stats.std:>5.2f} │ "
                  f"{stats.cv:>6.1f} │ {stats.threshold_flips:>5} │ {icon} {stats.verdict:<5} │")
        else:
            print(f"  │ {dim_name:<12} │   N/A │   N/A │    N/A │   N/A │ ⚠️ N/A   │")

    print("  └──────────────┴───────┴───────┴────────┴───────┴─────────┘")
    print()

    # 最终判定
    verdict_icons = {"pass": "✅ 通过", "fail": "❌ 不通过", "partial": "⚠️ 部分通过"}
    print(f"  最终判定: {verdict_icons.get(report.final_verdict, report.final_verdict)}")
    print()

    # 建议
    failing_dims = [k for k, v in report.aggregate_stats.items() if v.verdict == "fail"]
    if failing_dims:
        print(f"  ⚠️ 以下维度 CV >= 15%，建议禁用: {', '.join(failing_dims)}")
    if report.final_verdict == "pass":
        print("  所有维度可靠性验证通过，可进入下一阶段（条件触发集成）")
    elif report.final_verdict == "partial":
        passing = [k for k, v in report.aggregate_stats.items() if v.verdict == "pass"]
        print(f"  通过的维度可进入条件触发: {', '.join(passing)}")
        print(f"  未通过的维度建议删除或重新设计")
    else:
        print("  ❌ LLM 评审不可靠，建议删除 llm_evaluate() 并投资于规则层改进")

    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="LLM 评审可靠性验证工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 默认: 50 篇文档 × 3 次运行，成本上限 $5
  python -m noteforge.quality.llm_eval_validator

  # 小规模测试: 10 篇文档 × 2 次运行
  python -m noteforge.quality.llm_eval_validator --sample-size 10 --runs 2

  # 提高成本上限
  python -m noteforge.quality.llm_eval_validator --cost-cap 10.0
        """,
    )
    parser.add_argument("--sample-size", type=int, default=50,
                        help="采样文档数量（默认 50）")
    parser.add_argument("--runs", type=int, default=3,
                        help="每篇文档运行次数（默认 3）")
    parser.add_argument("--cost-cap", type=float, default=5.0,
                        help="成本上限美元（默认 $5.0，超过自动终止）")
    parser.add_argument("--notes-dir", type=str, default=None,
                        help="笔记目录（默认 output/notes/）")
    parser.add_argument("--transcripts-dir", type=str, default=None,
                        help="转写目录（默认 output/transcripts/）")
    parser.add_argument("--config", type=str, default=None,
                        help="llm_engine_config.yaml 路径")
    parser.add_argument("--verbose", action="store_true",
                        help="显示详细日志")

    args = parser.parse_args()

    # 配置日志
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    report = run_validation(
        sample_size=args.sample_size,
        runs_per_doc=args.runs,
        cost_cap_usd=args.cost_cap,
        notes_dir=args.notes_dir,
        transcripts_dir=args.transcripts_dir,
        config_path=args.config,
    )

    # 返回码：pass=0, partial=1, fail=2
    return {"pass": 0, "partial": 1, "fail": 2}.get(report.final_verdict, 2)


if __name__ == '__main__':
    sys.exit(main())
