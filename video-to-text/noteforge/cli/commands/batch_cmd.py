# -*- coding: utf-8 -*-
"""批量处理命令（支持断点续传、dry-run、质量阈值覆盖）"""

import os
import sys
from pathlib import Path
from typing import List, Optional

from noteforge.infra.execution_trace import ExecutionTrace


def _scan_transcripts(engine) -> List[str]:
    """扫描所有转写文件，返回排序后的路径列表"""
    return sorted(str(p) for p in engine._transcripts_dir.glob('*.txt'))


def _scan_existing_notes(engine) -> set:
    """扫描已有笔记的 stem 集合"""
    return {p.stem for p in engine._notes_dir.glob('*.md')}


def run_batch(engine, args):
    """批量模式（支持 --resume / --dry-run / --min-score / --max-retries）"""
    if args.title:
        print("[WARN] --title 在批量模式下被忽略")

    # ── 质量阈值覆盖 ──
    min_score_val = getattr(args, 'min_score', None)
    if isinstance(min_score_val, (int, float)):
        original_min_score = engine.min_score
        engine.min_score = float(min_score_val)
        print(f"[INFO] 质量阈值临时覆盖: {original_min_score:.0%} → {min_score_val:.0%}")

    max_retries_val = getattr(args, 'max_retries', None)
    if isinstance(max_retries_val, int) and not isinstance(max_retries_val, bool):
        original_max_retries = engine.max_retries
        engine.max_retries = max_retries_val
        print(f"[INFO] 最大重试次数临时覆盖: {original_max_retries} → {max_retries_val}")

    # ── dry-run 模式 ──
    if getattr(args, 'dry_run', False) is True:
        return _run_batch_dry_run(engine, args)

    # ── resume 模式 ──
    if getattr(args, 'resume', False) is True:
        return _run_batch_resume(engine, args)

    # ── 标准批量模式 ──
    results = engine.generate_batch(
        skip_existing=args.skip_existing,
        provider_override=args.provider,
        force=args.force,
        mode=args.mode,
        with_context=args.with_context,
        context_limit=args.context_limit,
    )
    failed = [r for r in results if r.error and r.error != "已存在（跳过）"]
    return 0 if not failed else 1


def _run_batch_dry_run(engine, args) -> int:
    """预览模式：扫描转写文件，打印计划，不调用 LLM"""
    transcript_paths = _scan_transcripts(engine)
    existing_notes = _scan_existing_notes(engine)

    if not transcript_paths:
        print("[INFO] 未找到转写文件")
        return 0

    skip = args.skip_existing and not args.force
    to_process = []
    to_skip = []

    for tpath in transcript_paths:
        stem = Path(tpath).stem
        if skip and stem in existing_notes:
            to_skip.append(stem)
        else:
            to_process.append(stem)

    print("\n" + "=" * 60)
    print("  DRY-RUN 预览模式（不调用 LLM）")
    print("=" * 60)
    print(f"  转写文件总数: {len(transcript_paths)}")
    print(f"  已有笔记（跳过）: {len(to_skip)}")
    print(f"  待处理: {len(to_process)}")
    print(f"  跳过已有: {'是' if skip else '否'}")
    print(f"  覆盖已有: {'是' if args.force else '否'}")
    print(f"  内容类型: {getattr(args, 'content_type', None) or 'auto'}")
    print(f"  质量阈值: {engine.min_score:.0%}")
    print(f"  最大重试: {engine.max_retries}")

    if to_process:
        print(f"\n  待处理文件列表:")
        for i, stem in enumerate(to_process, 1):
            print(f"    {i}. {stem}")

    if to_skip:
        print(f"\n  将跳过的文件（已有笔记）:")
        for i, stem in enumerate(to_skip, 1):
            print(f"    {i}. {stem}")

    print("=" * 60)
    return 0


def _run_batch_resume(engine, args) -> int:
    """断点续传模式：加载 ExecutionTrace，从上次完成的位置继续"""
    checkpoint_file = getattr(args, 'checkpoint_file', None)
    trace = ExecutionTrace(trace_dir=checkpoint_file)

    # 扫描所有转写文件
    transcript_paths = _scan_transcripts(engine)
    if not transcript_paths:
        print("[INFO] 未找到转写文件")
        return 0

    # 检查每个文件的追踪状态，确定哪些需要处理
    to_process = []
    already_done = []

    for tpath in transcript_paths:
        stem = Path(tpath).stem
        trace_id = f"batch_{stem}"

        # 尝试加载追踪记录
        records = trace.resume(trace_id)
        last_completed = trace.get_last_completed_stage(trace_id)

        if last_completed == 'evaluate':
            # 质量门禁已通过，视为完成
            already_done.append(stem)
        elif last_completed is not None:
            # 有部分完成，需要续传
            to_process.append((stem, tpath, last_completed))
        else:
            # 无追踪记录，需要从头处理
            to_process.append((stem, tpath, None))

    print("\n" + "=" * 60)
    print("  断点续传模式")
    print("=" * 60)
    print(f"  转写文件总数: {len(transcript_paths)}")
    print(f"  已完成（跳过）: {len(already_done)}")
    print(f"  待处理/续传: {len(to_process)}")

    if already_done:
        print(f"\n  已完成文件:")
        for i, stem in enumerate(already_done, 1):
            print(f"    {i}. {stem}")

    if to_process:
        print(f"\n  待处理文件:")
        for i, (stem, tpath, last_stage) in enumerate(to_process, 1):
            if last_stage:
                print(f"    {i}. {stem} (续传自: {last_stage})")
            else:
                print(f"    {i}. {stem} (新)")
    print("=" * 60)

    if not to_process:
        print("[INFO] 所有文件已处理完成")
        return 0

    # 只处理待处理的文件
    paths_to_process = [tpath for (_, tpath, _) in to_process]
    results = engine.generate_batch(
        transcript_paths=paths_to_process,
        skip_existing=False,  # resume 模式下不跳过，由追踪决定
        provider_override=args.provider,
        force=args.force,
        mode=args.mode,
        with_context=args.with_context,
        context_limit=args.context_limit,
    )
    failed = [r for r in results if r.error and r.error != "已存在（跳过）"]
    return 0 if not failed else 1
