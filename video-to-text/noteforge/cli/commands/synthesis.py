# -*- coding: utf-8 -*-
"""知识合成命令"""
import os


def run_synthesis(engine, args):
    """知识合成模式"""
    note_paths = None
    if args.input:
        # 解析输入为笔记路径
        note_paths = []
        for inp in args.input:
            if os.path.exists(inp):
                note_paths.append(inp)
            elif inp.startswith('ep'):
                candidate = engine.notes_dir / f"{inp}.md"
                if candidate.exists():
                    note_paths.append(str(candidate))

    result = engine.generate_synthesis(
        note_paths=note_paths,
        provider_override=args.provider
    )
    if result:
        print(f"\n[OK] 知识合成文档: {result}")
    else:
        print("\n[ERROR] 知识合成失败")
        return 1
    return 0


def run_synthesis_2stage(engine, args):
    """两阶段合成模式"""
    note_paths = None
    if args.input:
        note_paths = []
        for inp in args.input:
            if os.path.exists(inp):
                note_paths.append(inp)

    result = engine.generate_synthesis_two_stage(
        note_paths=note_paths,
        provider_override=args.provider,
        domain=getattr(args, 'domain', None),
    )
    if result:
        print(f"\n[OK] 两阶段合成文档: {result}")
        # 打印 token 统计
        engine.token_manager.print_summary()
    else:
        print("\n[ERROR] 两阶段合成失败")
        return 1
    return 0


def run_synthesis_incremental(engine, args):
    """增量更新模式"""
    if not args.input:
        print("[ERROR] 增量更新需要指定新增笔记路径 (--input)")
        return 1
    new_note = args.input[0]
    if not os.path.exists(new_note):
        # 尝试在 notes 目录查找
        candidate = engine.notes_dir / new_note
        if candidate.exists():
            new_note = str(candidate)
        else:
            print(f"[ERROR] 笔记文件不存在: {new_note}")
            return 1

    result = engine.update_synthesis_incremental(
        new_note_path=new_note,
        provider_override=args.provider
    )
    if result:
        print(f"\n[OK] 增量更新完成: {result}")
        engine.token_manager.print_summary()
    else:
        print("\n[ERROR] 增量更新失败")
        return 1
    return 0
