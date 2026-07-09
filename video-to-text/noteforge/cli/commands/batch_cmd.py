# -*- coding: utf-8 -*-
"""批量处理命令"""


def run_batch(engine, args):
    """批量模式"""
    if args.title:
        print("[WARN] --title 在批量模式下被忽略")
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
