# -*- coding: utf-8 -*-
"""质量检查命令"""
import os


def run_check_only(engine, args):
    """仅质量检查模式"""
    if not os.path.exists(args.check_only):
        print(f"[ERROR] 笔记文件不存在: {args.check_only}")
        return 1
    report = engine.check_only(args.check_only)
    if report is None:
        print("[ERROR] 质量检查失败（未找到对应转写文件）")
        return 1
    return 0 if report.get('overall_passed') else 1
