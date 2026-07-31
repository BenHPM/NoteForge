# -*- coding: utf-8 -*-
"""批量处理进度查看/管理命令"""

import os
import json
from pathlib import Path

from noteforge.infra.execution_trace import ExecutionTrace


def run_progress_show(args) -> int:
    """显示当前批量处理进度"""
    checkpoint_file = getattr(args, 'checkpoint_file', None)
    trace = ExecutionTrace(trace_dir=checkpoint_file)
    trace_dir = trace.trace_dir

    if not os.path.isdir(trace_dir):
        print("[INFO] 无进度数据（追踪目录不存在）")
        return 0

    # 扫描所有追踪文件
    trace_files = sorted(Path(trace_dir).glob('*.json'))
    if not trace_files:
        print("[INFO] 无进度数据（追踪目录为空）")
        return 0

    # 统计各状态
    completed = []
    failed = []
    running = []
    pending = []
    dead_letter = []

    for tf in trace_files:
        trace_id = tf.stem
        records = trace.resume(trace_id)
        if not records:
            continue

        # 取最后一条记录的状态
        last_rec = records[-1]
        status = last_rec.status

        entry = {
            'trace_id': trace_id,
            'stage': last_rec.stage,
            'status': status.value,
        }
        if last_rec.error_type:
            entry['error_type'] = last_rec.error_type
        if last_rec.completed_at:
            entry['completed_at'] = last_rec.completed_at

        if status == ExecutionTrace.Status.COMPLETED:
            completed.append(entry)
        elif status == ExecutionTrace.Status.FAILED:
            failed.append(entry)
        elif status == ExecutionTrace.Status.RUNNING:
            running.append(entry)
        elif status == ExecutionTrace.Status.PENDING:
            pending.append(entry)
        elif status == ExecutionTrace.Status.DEAD_LETTER:
            dead_letter.append(entry)

    total = len(completed) + len(failed) + len(running) + len(pending) + len(dead_letter)

    print("\n" + "=" * 60)
    print("  批量处理进度")
    print("=" * 60)
    print(f"  追踪目录: {trace_dir}")
    print(f"  追踪文件数: {total}")
    print(f"  ✅ 已完成: {len(completed)}")
    print(f"  ❌ 失败: {len(failed)}")
    print(f"  🔄 运行中: {len(running)}")
    print(f"  ⏳ 待处理: {len(pending)}")
    print(f"  ⛔ 死信: {len(dead_letter)}")

    if total > 0:
        pct = len(completed) / total * 100
        print(f"\n  总进度: {pct:.1f}% ({len(completed)}/{total})")

    if failed:
        print(f"\n  失败详情:")
        for entry in failed[:20]:
            err = entry.get('error_type', 'unknown')
            print(f"    {entry['trace_id']}: {entry['stage']} ({err})")

    if dead_letter:
        print(f"\n  死信详情（永久放弃）:")
        for entry in dead_letter[:20]:
            err = entry.get('error_type', 'unknown')
            print(f"    {entry['trace_id']}: {entry['stage']} ({err})")

    print("=" * 60)
    return 0


def run_progress_clear(args) -> int:
    """清除进度数据"""
    checkpoint_file = getattr(args, 'checkpoint_file', None)
    trace = ExecutionTrace(trace_dir=checkpoint_file)
    trace_dir = trace.trace_dir

    if not os.path.isdir(trace_dir):
        print("[INFO] 无进度数据可清除")
        return 0

    trace_files = list(Path(trace_dir).glob('*.json'))
    if not trace_files:
        print("[INFO] 无进度数据可清除")
        return 0

    count = 0
    for tf in trace_files:
        try:
            os.unlink(tf)
            count += 1
        except OSError as e:
            print(f"[WARN] 无法删除 {tf}: {e}")

    print(f"[INFO] 已清除 {count} 个追踪文件")
    return 0
