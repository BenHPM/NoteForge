# -*- coding: utf-8 -*-
"""NoteForge CLI 入口"""
import sys
import logging
import argparse

# 修复 Windows 控制台编码问题（subprocess 调用时 emoji 等 Unicode 字符）
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from noteforge.infra import env as env_check  # noqa: F401 — 检测 Python 环境（必须在其他 import 之前）
from noteforge.engine.note_engine import LLMNoteEngine
from noteforge.cli.commands import (
    run_check_only,
    run_search,
    run_list_notes,
    run_youtube,
    run_youtube_playlist,
    run_bilibili,
    run_audio_url,
    run_synthesis,
    run_synthesis_2stage,
    run_synthesis_incremental,
    run_podcast_subscribe,
    run_podcast_unsubscribe,
    run_podcast_list,
    run_podcast_sync,
    run_podcast_sync_all,
    run_podcast_process,
    run_batch,
    run_single_note,
)


def main():
    """CLI 入口"""
    parser = argparse.ArgumentParser(
        description='NoteForge LLM 笔记生成引擎',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python cli.py --input ep01\n"
            "  python cli.py --batch --skip-existing\n"
            "  python cli.py --input ep01 --force\n"
            "  python cli.py --check-only output/notes/ep01.md\n"
        )
    )

    parser.add_argument(
        '--input', nargs='+',
        help='转写文件路径、音频文件路径或集数编号（ep01, ep02, ...）'
    )
    parser.add_argument(
        '--youtube',
        help='YouTube 视频 URL（自动下载音频+转写+生成笔记）'
    )
    parser.add_argument(
        '--youtube-playlist',
        help='YouTube 播放列表 URL（批量下载+转写+生成笔记）'
    )
    parser.add_argument(
        '--bilibili',
        help='Bilibili 视频 URL 或 BV 号（自动下载音频+转写+生成笔记，无需 Cookie）'
    )
    parser.add_argument(
        '--audio-url',
        help='音频平台链接（小宇宙/喜马拉雅/荔枝FM 等，自动下载+转写+生成笔记）'
    )
    # Podcast RSS 订阅
    podcast_group = parser.add_argument_group('Podcast RSS 订阅')
    podcast_group.add_argument(
        '--podcast-subscribe', metavar='URL',
        help='订阅一个 podcast RSS feed（或主页 URL）'
    )
    podcast_group.add_argument(
        '--podcast-unsubscribe', metavar='NAME',
        help='取消订阅一个 podcast feed'
    )
    podcast_group.add_argument(
        '--podcast-list', action='store_true',
        help='列出所有已订阅的 feeds 和 episode 统计'
    )
    podcast_group.add_argument(
        '--podcast-sync', metavar='NAME',
        help='同步指定 feed: 获取新 episodes 列表'
    )
    podcast_group.add_argument(
        '--podcast-sync-all', action='store_true',
        help='同步所有已订阅的 feeds'
    )
    podcast_group.add_argument(
        '--podcast-process', metavar='NAME',
        help='下载+转写+生成笔记: 指定 feed 的所有新 episodes'
    )
    podcast_group.add_argument(
        '--podcast-max', type=int, default=0,
        help='--podcast-process 最多处理的 episode 数 (0=不限)'
    )
    podcast_group.add_argument(
        '--podcast-name', metavar='NAME',
        help='--podcast-subscribe 时手动指定 feed 名称'
    )
    parser.add_argument(
        '--mode', choices=['notes', 'synthesis', 'synthesis-2stage',
                           'synthesis-incremental', 'meeting'], default='notes',
        help='生成模式：notes=单集笔记, synthesis=跨集知识合成, meeting=会议纪要'
    )
    parser.add_argument(
        '--batch', action='store_true',
        help='批量处理所有转写文件'
    )
    parser.add_argument(
        '--skip-existing', action='store_true',
        help='跳过已有笔记的集数'
    )
    parser.add_argument(
        '--force', action='store_true',
        help='覆盖已有笔记'
    )
    parser.add_argument(
        '--check-only',
        help='仅运行质量检查（不生成笔记）'
    )
    parser.add_argument(
        '--config',
        help='自定义配置文件路径'
    )
    parser.add_argument(
        '--provider', choices=['claude', 'openai', 'local'],
        help='覆盖 LLM 提供商'
    )
    parser.add_argument(
        '--output-dir',
        help='覆盖输出目录'
    )
    parser.add_argument(
        '--title',
        help='手动指定笔记标题'
    )
    parser.add_argument(
        '--content-type',
        choices=['lecture', 'tutorial', 'interview', 'podcast', 'meeting'],
        help='内容类型（影响 prompt 策略和质量检查的领域概念加载）'
    )

    # 知识管理
    parser.add_argument(
        '--search',
        help='搜索笔记（关键词）'
    )
    parser.add_argument(
        '--tags', nargs='*',
        help='按标签过滤搜索结果'
    )
    parser.add_argument(
        '--list-notes', action='store_true',
        help='列出所有笔记（含标签和统计）'
    )
    parser.add_argument(
        '--with-context', action='store_true',
        help='生成时自动注入相关历史笔记作为上下文'
    )
    parser.add_argument(
        '--context-limit', type=int, default=3,
        help='关联笔记数量上限（默认 3）'
    )

    parser.add_argument(
        '--verbose', action='store_true',
        help='详细日志输出'
    )
    parser.add_argument(
        '--domain',
        help='指定知识域 ID（用于 synthesis-2stage，只合成该域笔记）'
    )

    args = parser.parse_args()

    # 验证参数
    has_action = (args.input or args.batch or args.check_only or
                  args.youtube or args.youtube_playlist or args.bilibili or args.audio_url or
                  args.mode in ('synthesis', 'synthesis-2stage', 'synthesis-incremental') or
                  args.search or args.list_notes or
                  args.podcast_subscribe or args.podcast_unsubscribe or
                  args.podcast_list or args.podcast_sync or
                  args.podcast_sync_all or args.podcast_process)
    if not has_action:
        parser.print_help()
        sys.exit(1)

    # 初始化引擎
    engine = LLMNoteEngine(config_path=args.config)

    if args.content_type:
        engine.configure(content_type=args.content_type)

    if args.verbose:
        logging.getLogger('noteforge').setLevel(logging.DEBUG)

    if args.output_dir:
        engine.configure(output_dir=args.output_dir)

    # 分支路由：各模式委托到 commands.py 中的独立函数
    exit_code = None

    if args.check_only:
        exit_code = run_check_only(engine, args)

    elif args.search:
        exit_code = run_search(engine, args)

    elif args.list_notes:
        exit_code = run_list_notes(engine, args)

    elif args.youtube:
        exit_code = run_youtube(engine, args)

    elif args.youtube_playlist:
        exit_code = run_youtube_playlist(engine, args)

    elif args.bilibili:
        exit_code = run_bilibili(engine, args)

    elif args.audio_url:
        exit_code = run_audio_url(engine, args)

    elif args.mode == 'synthesis':
        exit_code = run_synthesis(engine, args)

    elif args.mode == 'synthesis-2stage':
        exit_code = run_synthesis_2stage(engine, args)

    elif args.mode == 'synthesis-incremental':
        exit_code = run_synthesis_incremental(engine, args)

    elif args.podcast_subscribe:
        exit_code = run_podcast_subscribe(engine, args)

    elif args.podcast_unsubscribe:
        exit_code = run_podcast_unsubscribe(engine, args)

    elif args.podcast_list:
        exit_code = run_podcast_list(engine, args)

    elif args.podcast_sync:
        exit_code = run_podcast_sync(engine, args)

    elif args.podcast_sync_all:
        exit_code = run_podcast_sync_all(engine, args)

    elif args.podcast_process:
        exit_code = run_podcast_process(engine, args)

    elif args.batch:
        exit_code = run_batch(engine, args)

    elif args.input:
        exit_code = run_single_note(engine, args)

    if exit_code is not None:
        sys.exit(exit_code)


if __name__ == '__main__':
    main()
