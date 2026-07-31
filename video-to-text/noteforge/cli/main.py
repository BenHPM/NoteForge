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
    run_setup,
    run_doctor,
    run_health_check,
    run_health_check_asr,
    run_validate_config,
    run_progress_show,
    run_progress_clear,
    run_quality_view,
    run_quality_list,
    run_feishu_auth,
    run_feishu_validate,
    run_detect_domain,
    run_domain_list,
    run_incremental_update,
    run_cleanup,
    run_provider_status,
)


def main():
    """CLI 入口"""
    # 修复 Windows 控制台编码问题（仅在 CLI 入口执行，不在 import 时执行）
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

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
                           'synthesis-incremental', 'incremental-update', 'meeting'], default='notes',
        help='生成模式：notes=单集笔记, synthesis=跨集知识合成, meeting=会议纪要, incremental-update=按域增量合成'
    )
    parser.add_argument(
        '--batch', action='store_true',
        help='批量处理所有转写文件'
    )
    parser.add_argument(
        '--resume', action='store_true',
        help='断点续传（集成 auto_pipeline 能力）'
    )
    parser.add_argument(
        '--checkpoint-file',
        help='自定义进度文件路径'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='预览模式（不调用 LLM，只打印计划）'
    )
    parser.add_argument(
        '--min-score', type=float,
        help='临时覆盖质量阈值'
    )
    parser.add_argument(
        '--max-retries', type=int,
        help='临时覆盖最大重试次数'
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
        '--format', dest='format',
        choices=['json', 'md', 'table'],
        default='table',
        help='输出格式: json=JSON, md=Markdown, table=表格（默认 table）'
    )
    parser.add_argument(
        '--quality-view',
        help='查看笔记质量报告（指定笔记文件或报告 JSON 文件）'
    )
    parser.add_argument(
        '--quality-list', action='store_true',
        help='列出所有质量报告摘要'
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
        help='指定知识域 ID（用于 synthesis-2stage / incremental-update，只合成该域笔记）'
    )
    parser.add_argument(
        '--detect-domain', metavar='FILE',
        help='检测文件所属知识域'
    )
    parser.add_argument(
        '--domain-list', action='store_true',
        help='列出所有已配置的知识域'
    )

    # 环境与配置
    parser.add_argument(
        '--setup', action='store_true',
        help='一键创建隔离环境'
    )
    parser.add_argument(
        '--doctor', action='store_true',
        help='诊断环境，输出缺失项和修复建议'
    )
    parser.add_argument(
        '--validate-config', action='store_true',
        help='验证 YAML 配置完整性和有效性'
    )
    parser.add_argument(
        '--health-check', action='store_true',
        help='验证所有组件健康状态'
    )
    parser.add_argument(
        '--health-check-asr', action='store_true',
        help='仅验证 ASR 组件'
    )
    parser.add_argument(
        '--feishu-auth', action='store_true',
        help='引导 lark-cli 认证'
    )
    parser.add_argument(
        '--feishu-validate', action='store_true',
        help='验证飞书凭证和连接'
    )

    # Provider 状态
    parser.add_argument(
        '--provider-status', action='store_true',
        help='查看 LLM Provider 状态'
    )

    # 清理
    parser.add_argument(
        '--cleanup', action='store_true',
        help='清理临时文件'
    )
    parser.add_argument(
        '--cleanup-logs', action='store_true',
        help='清理旧日志'
    )
    parser.add_argument(
        '--cleanup-temp', action='store_true',
        help='清理临时目录'
    )
    parser.add_argument(
        '--cleanup-extractions', action='store_true',
        help='清理提取缓存'
    )
    parser.add_argument(
        '--cleanup-traces', action='store_true',
        help='清理执行追踪'
    )
    parser.add_argument(
        '--cleanup-all', action='store_true',
        help='清理所有临时文件'
    )

    # 进度管理子命令
    subparsers = parser.add_subparsers(dest='command')
    progress_parser = subparsers.add_parser('progress', help='查看/管理批量处理进度')
    progress_parser.add_argument('--show', action='store_true', help='显示当前批量处理进度')
    progress_parser.add_argument('--clear', action='store_true', help='清除进度数据')
    progress_parser.add_argument('--checkpoint-file', help='自定义进度文件路径')

    args = parser.parse_args()

    # 验证参数
    has_action = (args.input or args.batch or args.check_only or
                  args.youtube or args.youtube_playlist or args.bilibili or args.audio_url or args.local or
                  args.mode in ('synthesis', 'synthesis-2stage', 'synthesis-incremental', 'incremental-update') or
                  args.search or args.list_notes or
                  args.podcast_subscribe or args.podcast_unsubscribe or
                  args.podcast_list or args.podcast_sync or
                  args.podcast_sync_all or args.podcast_process or
                  args.setup or args.doctor or args.validate_config or
                  args.health_check or args.health_check_asr or
                  args.feishu_auth or args.feishu_validate or
                  args.quality_view or args.quality_list or
                  args.detect_domain or args.domain_list or
                  args.provider_status or args.cleanup or
                  args.cleanup_logs or args.cleanup_temp or
                  args.cleanup_extractions or args.cleanup_traces or
                  args.cleanup_all or
                  args.command == 'progress')
    if not has_action:
        parser.print_help()
        sys.exit(1)

    # 环境诊断/设置命令：不需要引擎，跳过 check_env
    if args.setup:
        sys.exit(run_setup(args))
    if args.doctor:
        sys.exit(run_doctor(args))
    if args.validate_config:
        sys.exit(run_validate_config(args))
    if args.health_check:
        sys.exit(run_health_check(args))
    if args.health_check_asr:
        sys.exit(run_health_check_asr(args))

    # 飞书认证/验证命令：不需要引擎
    if args.feishu_auth:
        sys.exit(run_feishu_auth(args))
    if args.feishu_validate:
        sys.exit(run_feishu_validate(args))

    # 进度管理命令：不需要引擎
    if args.command == 'progress':
        if getattr(args, 'show', False):
            sys.exit(run_progress_show(args))
        elif getattr(args, 'clear', False):
            sys.exit(run_progress_clear(args))
        else:
            print("[INFO] 请指定 --show 或 --clear")
            sys.exit(1)

    # 质量报告查看/列表命令：不需要引擎
    if args.quality_view:
        sys.exit(run_quality_view(args))
    if args.quality_list:
        sys.exit(run_quality_list(args))

    # 知识域检测/列表命令：不需要引擎
    if args.detect_domain:
        sys.exit(run_detect_domain(args))
    if args.domain_list:
        sys.exit(run_domain_list(args))

    # Provider 状态命令：不需要引擎
    if args.provider_status:
        sys.exit(run_provider_status(args))

    # 清理命令：不需要引擎
    if args.cleanup or args.cleanup_logs or args.cleanup_temp or args.cleanup_extractions or args.cleanup_traces or args.cleanup_all:
        sys.exit(run_cleanup(args))

    # 惰性检查：确保在正确的 Python 环境中运行
    from noteforge.infra.env import check_env
    try:
        check_env()
    except EnvironmentError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
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
        (lambda a: a.mode == 'incremental-update', run_incremental_update),
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
