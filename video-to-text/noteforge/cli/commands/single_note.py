# -*- coding: utf-8 -*-
"""单文件/多文件笔记生成命令"""
import os
import re

from noteforge.cli.commands._shared import _show_cached_quality


def run_single_note(engine, args):
    """单文件/多文件模式"""
    # 解析输入（可能是文件路径、epXX 编号或文件名关键词）
    transcript_paths = []
    for inp in args.input:
        if os.path.exists(inp):
            transcript_paths.append(inp)
        elif inp.startswith('ep'):
            candidate = engine.transcripts_dir / f"{inp}.txt"
            if candidate.exists():
                transcript_paths.append(str(candidate))
            else:
                print(f"[ERROR] 未找到转写文件: {candidate}")
        else:
            # 关键词模糊匹配：在 transcripts 目录搜索包含关键词的文件
            # 解决中文引号等特殊字符在命令行中无法正确传递的问题
            matches = []
            norm_inp = re.sub(r'["""]', '"', inp)  # 统一引号
            for f in engine.transcripts_dir.glob('*.txt'):
                fname = f.stem
                norm_fname = re.sub(r'["""]', '"', fname)
                if norm_inp in norm_fname:
                    matches.append(str(f))
            if len(matches) == 1:
                transcript_paths.append(matches[0])
                engine.logger.info(f"关键词 '{inp}' 匹配到: {os.path.basename(matches[0])}")
            elif len(matches) > 1:
                print(f"[WARN] 关键词 '{inp}' 匹配到 {len(matches)} 个文件:")
                for m in matches:
                    print(f"  - {os.path.basename(m)}")
                print("[提示] 请使用更精确的关键词或直接使用文件路径")
            else:
                # 同时检查 notes 目录（用于合成模式传入笔记路径）
                note_matches = []
                for f in engine.notes_dir.glob('*.md'):
                    fname = f.stem
                    norm_fname = re.sub(r'["""]', '"', fname)
                    if norm_inp in norm_fname:
                        note_matches.append(str(f))
                if len(note_matches) == 1:
                    transcript_paths.append(note_matches[0])
                    engine.logger.info(f"关键词 '{inp}' 在笔记目录匹配到: {os.path.basename(note_matches[0])}")
                elif len(note_matches) > 1:
                    print(f"[WARN] 关键词 '{inp}' 在笔记目录匹配到 {len(note_matches)} 个文件")
                else:
                    print(f"[ERROR] 无效输入: {inp}")

    if not transcript_paths:
        print("[ERROR] 没有有效的输入文件")
        return 1

    if len(transcript_paths) > 1 and args.title:
        print("[WARN] --title 在多文件输入模式下被忽略")

    if len(transcript_paths) == 1:
        result = engine.generate_note(
            transcript_paths[0],
            provider_override=args.provider,
            force=args.force,
            with_context=args.with_context,
            context_limit=args.context_limit,
            mode=args.mode,
        )
        # 单文件模式：立即触发待合成域的合成
        engine.flush_pending_synthesis()
        if result.error and result.error != "已存在（使用 --force 覆盖）":
            if '已存在' in result.error:
                _show_cached_quality(engine, result.note_path)
            else:
                print(f"\n[ERROR] {result.error}")
                return 1
        if result.total_score > 0:
            engine.quality_manager.print_quality_report(
                {'total_score': result.total_score,
                 'overall_passed': result.overall_passed,
                 'rule_results': {}}
            )
    else:
        results = engine.generate_batch(
            transcript_paths=transcript_paths,
            skip_existing=not args.force,
            provider_override=args.provider,
            force=args.force,
            mode=args.mode,
            with_context=args.with_context,
            context_limit=args.context_limit,
        )
        failed = [r for r in results if r.error and "已存在" not in r.error]
        return 0 if not failed else 1
    return 0
