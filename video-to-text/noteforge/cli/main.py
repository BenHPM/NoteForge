# -*- coding: utf-8 -*-
"""NoteForge CLI 入口"""
import sys
import logging
import argparse

from noteforge.engine.note_engine import LLMNoteEngine
from noteforge.cli.commands import (
    run_check_only,
    run_search,
    run_list_notes,
    run_youtube,
    run_youtube_playlist,
    run_bilibili,
    run_audio_url,
    run_local,
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
    # 修复 Windows 控制台编码问题（仅在 CLI 入口执行，不在 import 时执行）
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    # 惰性检查：确保在正确的 Python 环境中运行
    from noteforge.infra.env import check_env
    try:
        check_env()
    except EnvironmentError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

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
        '--bilibili', nargs='+',
        help='Bilibili 视频 URL 或 BV 号（支持多个，自动下载音频+转写+生成笔记，无需 Cookie）'
    )
    parser.add_argument(
        '--audio-url',
        help='音频平台链接（小宇宙/喜马拉雅/荔枝FM 等，自动下载+转写+生成笔记）'
    )
    parser.add_argument(
        '--local',
        help='本地音频/视频文件路径（.mp3/.wav/.m4a/.flac/.mp4/.mkv/.avi/.mov，直接转写+生成笔记）'
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
                  args.youtube or args.youtube_playlist or args.bilibili or args.audio_url or args.local or
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

    # 分支路由：按优先级检查各参数，委托到对应的 command 函数
    exit_code = None

    def _dispatch():
        nonlocal exit_code
        for flag_value, handler in _DISPATCH_TABLE:
            if flag_value(args):
                exit_code = handler(engine, args)
                return
        # 兜底：无匹配时打印帮助
        parser.print_help()

    _DISPATCH_TABLE = [
        (lambda a: a.check_only, run_check_only),
        (lambda a: a.search, run_search),
        (lambda a: a.list_notes, run_list_notes),
        (lambda a: a.youtube, run_youtube),
        (lambda a: a.youtube_playlist, run_youtube_playlist),
        (lambda a: a.bilibili, run_bilibili),
        (lambda a: a.audio_url, run_audio_url),
        (lambda a: a.local, run_local),
        (lambda a: a.mode == 'synthesis', run_synthesis),
        (lambda a: a.mode == 'synthesis-2stage', run_synthesis_2stage),
        (lambda a: a.mode == 'synthesis-incremental', run_synthesis_incremental),
        (lambda a: a.podcast_subscribe, run_podcast_subscribe),
        (lambda a: a.podcast_unsubscribe, run_podcast_unsubscribe),
        (lambda a: a.podcast_list, run_podcast_list),
        (lambda a: a.podcast_sync, run_podcast_sync),
        (lambda a: a.podcast_sync_all, run_podcast_sync_all),
        (lambda a: a.podcast_process, run_podcast_process),
        (lambda a: a.batch, run_batch),
        (lambda a: a.input, run_single_note),
    ]
    _dispatch()

    if exit_code is not None:
        sys.exit(exit_code)


if __name__ == '__main__':
    main()
