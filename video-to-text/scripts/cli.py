# -*- coding: utf-8 -*-
"""NoteForge CLI 入口"""
import os
import sys
import re
import json
import logging
import argparse
import subprocess
from pathlib import Path

# 修复 Windows 控制台编码问题（subprocess 调用时 emoji 等 Unicode 字符）
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import env_check  # noqa: F401 — 检测 Python 环境（必须在其他 import 之前）
from llm_note_engine import LLMNoteEngine


class MediaDownloader:
    """音频平台下载策略（yt-dlp + 平台 API 降级）"""

    @staticmethod
    def try_ytdlp(url, out_dir):
        """尝试 yt-dlp 下载，返回 audio_path 或 None"""
        import shutil
        if not shutil.which('yt-dlp'):
            return None
        output_tpl = os.path.join(out_dir, '%(title)s.%(ext)s')
        dl_cmd = [
            "yt-dlp", "--no-update",
            "--extract-audio", "--audio-format", "mp3",
            "--no-playlist", "-o", output_tpl, url,
        ]
        dl = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=600)
        if dl.returncode != 0:
            return None
        for line in (dl.stdout + dl.stderr).splitlines():
            if '[ExtractAudio]' in line and 'Destination:' in line:
                p = line.split('Destination:', 1)[1].strip()
                if os.path.exists(p):
                    return p
        # 回退：找最新 mp3
        import glob as _glob
        candidates = _glob.glob(os.path.join(out_dir, '*.mp3'))
        return max(candidates, key=os.path.getmtime) if candidates else None

    @staticmethod
    def try_xiaoyuzhou(url, out_dir):
        """小宇宙 API 提取，返回 (audio_path, title) 或 None"""
        import urllib.request
        m = re.search(r'xiaoyuzhoufm\.com/episode/([a-f0-9]+)', url)
        if not m:
            return None
        eid = m.group(1)
        api = f"https://www.xiaoyuzhoufm.com/api/v1/episode/get?eid={eid}"
        req = urllib.request.Request(api, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        ep = data.get('data', data)
        media = ep.get('media', {})
        audio_url = media.get('src') or ep.get('enclosure', {}).get('url', '')
        if not audio_url:
            return None
        title = ep.get('title', '')
        ext = os.path.splitext(audio_url.split('?')[0])[1] or '.mp3'
        if not ext.startswith('.'):
            ext = '.' + ext
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', title or eid)
        output_path = os.path.join(out_dir, f"{safe_title}{ext}")
        req2 = urllib.request.Request(audio_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://www.xiaoyuzhoufm.com/",
        })
        with urllib.request.urlopen(req2, timeout=300) as resp:
            with open(output_path, 'wb') as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
        return (output_path, title) if os.path.exists(output_path) else None

    @staticmethod
    def try_lizhi(url, out_dir):
        """荔枝FM API 提取，返回 (audio_path, title) 或 None"""
        import urllib.request
        m = re.search(r'lizhi\.fm/(?:episode/)?(\d+)', url)
        if not m:
            return None
        ep_id = m.group(1)
        api = f"https://www.lizhi.fm/api/audios/episode/{ep_id}"
        req = urllib.request.Request(api, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://www.lizhi.fm/",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        audio_url = data.get('data', {}).get('audio_url', '')
        if not audio_url:
            return None
        title = data.get('data', {}).get('title', '')
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', title or ep_id)
        output_path = os.path.join(out_dir, f"{safe_title}.mp3")
        req2 = urllib.request.Request(audio_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://www.lizhi.fm/",
        })
        with urllib.request.urlopen(req2, timeout=300) as resp:
            with open(output_path, 'wb') as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
        return (output_path, title) if os.path.exists(output_path) else None


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
                  args.mode == 'synthesis' or
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
        engine._content_type = args.content_type
        engine._quality_manager._content_type = args.content_type

    if args.verbose:
        logging.getLogger('noteforge').setLevel(logging.DEBUG)

    if args.output_dir:
        out = Path(args.output_dir)
        engine.notes_dir = out / 'notes'
        engine.reports_dir = out / 'quality_reports'
        engine.logs_dir = out / 'logs'
        for d in (engine.notes_dir, engine.reports_dir, engine.logs_dir):
            d.mkdir(parents=True, exist_ok=True)
        # 同步更新已提取组件的路径引用
        engine._batch_processor._notes_dir = engine.notes_dir
        engine._external_sync._notes_dir = engine.notes_dir
        engine._quality_manager._notes_dir = engine.notes_dir
        engine._quality_manager._reports_dir = engine.reports_dir
        engine._domain_classifier._notes_dir = engine.notes_dir
        engine._synthesis_engine._notes_dir = engine.notes_dir

    # 仅质量检查模式
    if args.check_only:
        if not os.path.exists(args.check_only):
            print(f"[ERROR] 笔记文件不存在: {args.check_only}")
            sys.exit(1)
        report = engine.check_only(args.check_only)
        if report is None:
            print("[ERROR] 质量检查失败（未找到对应转写文件）")
            sys.exit(1)
        sys.exit(0 if report.get('overall_passed') else 1)

    # 笔记搜索
    if args.search:
        from knowledge_index import KnowledgeIndex
        idx = KnowledgeIndex(str(engine.notes_dir))
        results = idx.search(args.search, tags=args.tags)
        if not results:
            print(f"\n未找到匹配 '{args.search}' 的笔记")
        else:
            print(f"\n搜索 '{args.search}' 找到 {len(results)} 条结果:\n")
            for i, r in enumerate(results, 1):
                print(f"  {i}. [{r.date}] {r.title}")
                print(f"     相关度: {r.relevance:.0%} | 标签: {', '.join(r.tags[:5])}")
                print(f"     {r.snippet[:120]}")
                print()
        sys.exit(0)

    # 笔记库概览
    if args.list_notes:
        from knowledge_index import KnowledgeIndex
        idx = KnowledgeIndex(str(engine.notes_dir))
        notes = idx.list_notes()
        tags = idx.get_all_tags()
        if not notes:
            print("\n笔记库为空")
        else:
            print(f"\n{'='*60}")
            print(f"  笔记库概览 ({len(notes)} 篇)")
            print(f"{'='*60}\n")
            for n in notes:
                print(f"  [{n.date}] {n.title}")
                print(f"     {n.char_count} 字 | 框架: {len(n.key_frameworks)} | 行动项: {len(n.action_items)}")
                if n.tags:
                    print(f"     标签: {', '.join(n.tags[:5])}")
            if tags:
                print(f"\n  --- 热门标签 ---")
                tag_str = ' | '.join(f"{t}({c})" for t, c in list(tags.items())[:15])
                print(f"  {tag_str}")
            print(f"\n{'='*60}")
        sys.exit(0)

    # YouTube 单视频模式
    if args.youtube:
        try:
            from youtube_handler import YouTubeHandler
            yt = YouTubeHandler(
                output_dir=str(engine.base_dir / 'output' / 'audio'),
                temp_dir=str(engine.base_dir / 'temp')
            )
            metadata = yt.download_audio(args.youtube)
            audio_path = metadata['path']
            title = args.title or metadata.get('title', '')
            engine.logger.info(f"YouTube 下载完成: {title}")
            result = engine.generate_note(
                audio_path, title=title,
                provider_override=args.provider, force=args.force,
                mode=args.mode,
                with_context=args.with_context,
                context_limit=args.context_limit,
            )
            if result.error:
                print(f"\n[ERROR] {result.error}")
                sys.exit(1)
        except Exception as e:
            print(f"\n[ERROR] YouTube 处理失败: {e}")
            sys.exit(1)
        sys.exit(0)

    # YouTube 播放列表模式
    if args.youtube_playlist:
        try:
            from youtube_handler import YouTubeHandler
            yt = YouTubeHandler(
                output_dir=str(engine.base_dir / 'output' / 'audio'),
                temp_dir=str(engine.base_dir / 'temp')
            )
            results_list = yt.download_playlist(args.youtube_playlist)
            success = [r for r in results_list if 'error' not in r]
            print(f"\n下载完成: {len(success)}/{len(results_list)} 个视频")
            # 对每个下载成功的音频生成笔记
            gen_results = []
            for meta in success:
                r = engine.generate_note(
                    meta['path'],
                    title=meta.get('title', ''),
                    provider_override=args.provider, force=args.force,
                    mode=args.mode,
                    with_context=args.with_context,
                    context_limit=args.context_limit,
                )
                gen_results.append(r)
            engine._print_batch_summary(gen_results)
        except Exception as e:
            print(f"\n[ERROR] YouTube 播放列表处理失败: {e}")
            sys.exit(1)
        sys.exit(0)

    # Bilibili 视频模式
    if args.bilibili:
        try:
            from bilibili_download import download_bilibili
            print(f"\n[Bilibili] 开始处理: {args.bilibili}")
            metadata = download_bilibili(args.bilibili)
            if not metadata.get('success'):
                print(f"\n[ERROR] {metadata.get('error', '下载失败')}")
                engine.logger.error(f"Bilibili 下载失败: {metadata.get('error', '未知')}")
                sys.exit(1)
            audio_path = metadata['path']
            title = args.title or metadata.get('title', '')
            method = metadata.get('method', 'unknown')
            engine.logger.info(f"Bilibili 下载完成: {title} (方法: {method})")
            print(f"  [INFO] 下载方式: {method}")
            result = engine.generate_note(
                audio_path, title=title,
                provider_override=args.provider, force=args.force,
                mode=args.mode,
                with_context=args.with_context,
                context_limit=args.context_limit,
            )
            if result.error and result.error != "已存在（使用 --force 覆盖）":
                print(f"\n[ERROR] {result.error}")
                sys.exit(1)
        except Exception as e:
            print(f"\n[ERROR] Bilibili 处理失败: {e}")
            engine.logger.error(f"Bilibili 处理异常: {e}", exc_info=True)
            sys.exit(1)
        sys.exit(0)

    # 音频平台链接模式（小宇宙/喜马拉雅/荔枝FM 等）
    if args.audio_url:

        output_dir_audio = str(engine.base_dir / 'output' / 'audio')
        os.makedirs(output_dir_audio, exist_ok=True)

        # --- 主流程：降级链 ---
        try:
            url = args.audio_url
            audio_path = None
            title = ""

            # 策略 1: yt-dlp（喜马拉雅原生支持，其他平台通用提取）
            print(f"\n  [策略1] yt-dlp 下载: {url}")
            engine.logger.info(f"音频平台: yt-dlp 尝试 {url}")
            result_path = MediaDownloader.try_ytdlp(url, output_dir_audio)
            if result_path:
                audio_path = result_path
                title = os.path.splitext(os.path.basename(audio_path))[0]
                print(f"  [OK] yt-dlp 成功")

            # 策略 2: 平台专用 API
            if not audio_path:
                if 'xiaoyuzhoufm.com' in url:
                    print(f"  [策略2] 小宇宙 API 提取...")
                    r = MediaDownloader.try_xiaoyuzhou(url, output_dir_audio)
                    if r:
                        audio_path, title = r
                        print(f"  [OK] 小宇宙 API 成功")
                elif 'lizhi.fm' in url:
                    print(f"  [策略2] 荔枝FM API 提取...")
                    r = MediaDownloader.try_lizhi(url, output_dir_audio)
                    if r:
                        audio_path, title = r
                        print(f"  [OK] 荔枝FM API 成功")
                elif 'ximalaya.com' in url:
                    # 喜马拉雅仅依赖 yt-dlp（已内置提取器），无 API 降级
                    if '/album/' in url:
                        print(f"  [提示] 喜马拉雅专辑链接不支持，请使用单集 /track/ 链接")
                    else:
                        print(f"  [提示] yt-dlp 不支持该喜马拉雅链接，可能是付费内容或链接格式有误")

            if not audio_path or not os.path.exists(audio_path):
                print(f"\n[ERROR] 所有下载策略均失败。请检查链接是否有效。")
                engine.logger.error(f"音频平台下载失败: {url}")
                sys.exit(1)

            title = args.title or title
            engine.logger.info(f"音频平台: 下载完成 {title}")
            print(f"  音频: {audio_path}")
            result = engine.generate_note(
                audio_path, title=title,
                provider_override=args.provider, force=args.force,
                mode=args.mode,
                with_context=args.with_context,
                context_limit=args.context_limit,
            )
            if result.error:
                print(f"\n[ERROR] {result.error}")
                sys.exit(1)
        except subprocess.TimeoutExpired:
            print("\n[ERROR] 下载超时")
            sys.exit(1)
        except Exception as e:
            print(f"\n[ERROR] 音频平台处理失败: {e}")
            engine.logger.error(f"音频平台处理异常: {e}", exc_info=True)
            sys.exit(1)
        sys.exit(0)

    # 知识合成模式
    if args.mode == 'synthesis':
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
            sys.exit(1)
        sys.exit(0)

    # 两阶段合成模式
    if args.mode == 'synthesis-2stage':
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
            sys.exit(1)
        sys.exit(0)

    # 增量更新模式
    if args.mode == 'synthesis-incremental':
        if not args.input:
            print("[ERROR] 增量更新需要指定新增笔记路径 (--input)")
            sys.exit(1)
        new_note = args.input[0]
        if not os.path.exists(new_note):
            # 尝试在 notes 目录查找
            candidate = engine.notes_dir / new_note
            if candidate.exists():
                new_note = str(candidate)
            else:
                print(f"[ERROR] 笔记文件不存在: {new_note}")
                sys.exit(1)

        result = engine.update_synthesis_incremental(
            new_note_path=new_note,
            provider_override=args.provider
        )
        if result:
            print(f"\n[OK] 增量更新完成: {result}")
            engine.token_manager.print_summary()
        else:
            print("\n[ERROR] 增量更新失败")
            sys.exit(1)
        sys.exit(0)

    # Podcast RSS 操作
    podcast_config = str(engine.base_dir / 'config' / 'podcast_feeds.json')
    podcast_audio = str(engine.base_dir / 'output' / 'audio' / 'podcasts')
    podcast_temp = str(engine.base_dir / 'temp')

    if args.podcast_subscribe:
        from podcast_handler import PodcastHandler
        ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
        try:
            info = ph.subscribe(args.podcast_subscribe, name=args.podcast_name)
            print(f"\n[OK] 已订阅: {info['name']}")
            print(f"     Feed URL: {info['feed_url']}")
            print(f"     Episodes: {info['episode_count']}")
        except Exception as e:
            print(f"\n[ERROR] 订阅失败: {e}")
            sys.exit(1)
        sys.exit(0)

    if args.podcast_unsubscribe:
        from podcast_handler import PodcastHandler
        ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
        try:
            ph.unsubscribe(args.podcast_unsubscribe)
            print(f"\n[OK] 已取消订阅: {args.podcast_unsubscribe}")
        except Exception as e:
            print(f"\n[ERROR] {e}")
            sys.exit(1)
        sys.exit(0)

    if args.podcast_list:
        from podcast_handler import PodcastHandler
        ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
        feeds = ph.list_feeds()
        if not feeds:
            print("\n尚未订阅任何 Podcast。使用 --podcast-subscribe URL 添加。")
        else:
            print(f"\n已订阅 {len(feeds)} 个 Podcast:")
            print("-" * 60)
            for f in feeds:
                print(f"  {f['slug']}")
                print(f"    名称: {f['name']}")
                print(f"    Episodes: {f['total_episodes']} "
                      f"(已处理: {f['processed']}, 新: {f['new']})")
                print(f"    最后同步: {f['last_synced'][:19] if f['last_synced'] else '未同步'}")
            print("-" * 60)
        sys.exit(0)

    if args.podcast_sync:
        from podcast_handler import PodcastHandler
        ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
        try:
            config = ph._load_feeds_config()
            if args.podcast_sync not in config['feeds']:
                print(f"\n[ERROR] 未找到订阅: {args.podcast_sync}")
                sys.exit(1)
            episodes = ph.list_episodes(args.podcast_sync, only_new=True)
            feed_name = config['feeds'][args.podcast_sync].get('name', args.podcast_sync)
            total = len(config['feeds'][args.podcast_sync].get('episodes', {}))
            print(f"\n{feed_name}: {len(episodes)}/{total} 个新 episode")
            for i, ep in enumerate(episodes[:20], 1):
                print(f"  {i}. {ep.title[:60]} [{ep.duration}]")
            if len(episodes) > 20:
                print(f"  ... 还有 {len(episodes) - 20} 个")
        except Exception as e:
            print(f"\n[ERROR] 同步失败: {e}")
            sys.exit(1)
        sys.exit(0)

    if args.podcast_sync_all:
        from podcast_handler import PodcastHandler
        ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
        config = ph._load_feeds_config()
        if not config['feeds']:
            print("\n尚未订阅任何 Podcast。")
            sys.exit(0)
        print(f"\n同步 {len(config['feeds'])} 个 Podcast:")
        for slug, feed in config['feeds'].items():
            episodes = ph.list_episodes(slug, only_new=True)
            total = len(feed.get('episodes', {}))
            print(f"  {slug}: {len(episodes)}/{total} 个新 episode")
        sys.exit(0)

    if args.podcast_process:
        from podcast_handler import PodcastHandler
        ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
        try:
            # 先同步
            config = ph._load_feeds_config()
            if args.podcast_process not in config['feeds']:
                print(f"\n[ERROR] 未找到订阅: {args.podcast_process}")
                sys.exit(1)
            feed_url = config['feeds'][args.podcast_process]['feed_url']
            ph.subscribe(feed_url, name=args.podcast_process)

            # 下载新 episodes
            episodes = ph.download_new_episodes(args.podcast_process)
            if args.podcast_max > 0:
                episodes = episodes[:args.podcast_max]

            if not episodes:
                print("\n没有新 episode 需要处理。")
                sys.exit(0)

            print(f"\n处理 {len(episodes)} 个 episodes...")
            gen_results = []
            for i, ep in enumerate(episodes, 1):
                engine.logger.info(f"[{i}/{len(episodes)}] {ep.title}")
                result = engine.generate_note(
                    ep.local_audio_path, title=ep.title,
                    provider_override=args.provider, force=args.force,
                    mode=args.mode,
                    with_context=args.with_context,
                    context_limit=args.context_limit,
                )
                if result and not result.error:
                    ph.mark_episode_processed(
                        args.podcast_process, ep.guid,
                        local_audio_path=ep.local_audio_path,
                        note_path=result.note_path
                    )
                gen_results.append(result)
            engine._print_batch_summary(gen_results)
        except Exception as e:
            print(f"\n[ERROR] Podcast 处理失败: {e}")
            sys.exit(1)
        sys.exit(0)

    # 批量模式
    if args.batch:
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
        sys.exit(0 if not failed else 1)

    # 单文件/多文件模式
    if args.input:
        # 解析输入（可能是文件路径或 epXX 编号）
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
                print(f"[ERROR] 无效输入: {inp}")

        if not transcript_paths:
            print("[ERROR] 没有有效的输入文件")
            sys.exit(1)

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
            if result.error and result.error != "已存在（使用 --force 覆盖）":
                print(f"\n[ERROR] {result.error}")
                sys.exit(1)
            if result.total_score > 0:
                engine._quality_manager.print_quality_report(
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
            sys.exit(0 if not failed else 1)


if __name__ == '__main__':
    main()
