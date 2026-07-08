# -*- coding: utf-8 -*-
"""NoteForge CLI 各模式的执行逻辑（从 main.py 提取）"""
import os
import sys
import re
import logging
import subprocess

from noteforge.sources.downloader import MediaDownloader


def _show_cached_quality(engine, note_path: str):
    """缓存跳过时自动运行质量检查并显示摘要"""
    if not note_path or not os.path.exists(note_path):
        return
    print(f"  [INFO] 笔记已存在: {os.path.basename(note_path)}")
    try:
        report = engine.check_only(note_path)
        if report:
            score = report.get('total_score', 0)
            passed = report.get('overall_passed', False)
            print(f"  [质量] 总分: {score:.0%} | {'✅ 通过' if passed else '❌ 未通过'}")
    except Exception:
        pass  # 质量检查失败不影响主流程


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


def run_search(engine, args):
    """笔记搜索"""
    from noteforge.intelligence.knowledge_index import KnowledgeIndex
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
    return 0


def run_list_notes(engine, args):
    """笔记库概览"""
    from noteforge.intelligence.knowledge_index import KnowledgeIndex
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
    return 0


def run_youtube(engine, args):
    """YouTube 单视频模式"""
    try:
        from noteforge.sources.youtube import YouTubeHandler
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
        # 单视频模式：立即触发待合成域的合成
        engine.flush_pending_synthesis()
        if result.error:
            if '已存在' in result.error:
                _show_cached_quality(engine, result.note_path)
            else:
                print(f"\n[ERROR] {result.error}")
                return 1
    except Exception as e:
        print(f"\n[ERROR] YouTube 处理失败: {e}")
        return 1
    return 0


def run_youtube_playlist(engine, args):
    """YouTube 播放列表模式"""
    try:
        from noteforge.sources.youtube import YouTubeHandler
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
        # 播放列表模式：所有笔记生成后统一触发合成
        engine.flush_pending_synthesis()
        engine.print_batch_summary(gen_results)
    except Exception as e:
        print(f"\n[ERROR] YouTube 播放列表处理失败: {e}")
        return 1
    return 0


def run_bilibili(engine, args):
    """Bilibili 视频模式（支持多 URL，批量模式统一合成）"""
    from noteforge.sources.bilibili import download_bilibili
    urls = args.bilibili if isinstance(args.bilibili, list) else [args.bilibili]

    if len(urls) > 1:
        print(f"\n[Bilibili] 批量处理 {len(urls)} 个视频")

    # 用批量模式处理，避免每篇触发独立合成
    gen_results = []
    errors = 0

    for i, url in enumerate(urls, 1):
        try:
            prefix = f"[{i}/{len(urls)}] " if len(urls) > 1 else ""
            print(f"\n{prefix}[Bilibili] 开始处理: {url}")
            metadata = download_bilibili(url)
            if not metadata.get('success'):
                print(f"\n{prefix}[ERROR] {metadata.get('error', '下载失败')}")
                engine.logger.error(f"Bilibili 下载失败: {metadata.get('error', '未知')}")
                errors += 1
                continue
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
            gen_results.append(result)
            if result.error and result.error != "已存在（使用 --force 覆盖）":
                if '已存在' in result.error:
                    _show_cached_quality(engine, result.note_path)
                else:
                    print(f"\n{prefix}[ERROR] {result.error}")
                    errors += 1
        except Exception as e:
            print(f"\n[ERROR] Bilibili 处理失败: {e}")
            engine.logger.error(f"Bilibili 处理异常: {e}", exc_info=True)
            errors += 1

    # 批量完成后统一触发合成
    if gen_results:
        engine.flush_pending_synthesis()

    if gen_results:
        engine.print_batch_summary(gen_results)

    return 1 if errors else 0


def run_audio_url(engine, args):
    """音频平台链接模式（小宇宙/喜马拉雅/荔枝FM 等）"""
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
            return 1

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
        # 单音频模式：立即触发待合成域的合成
        engine.flush_pending_synthesis()
        if result.error:
            if '已存在' in result.error:
                _show_cached_quality(engine, result.note_path)
            else:
                print(f"\n[ERROR] {result.error}")
                return 1
    except subprocess.TimeoutExpired:
        print("\n[ERROR] 下载超时")
        return 1
    except Exception as e:
        print(f"\n[ERROR] 音频平台处理失败: {e}")
        engine.logger.error(f"音频平台处理异常: {e}", exc_info=True)
        return 1
    return 0


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


def _get_podcast_dirs(engine):
    """返回 (podcast_config, podcast_audio, podcast_temp) 路径元组"""
    return (
        str(engine.base_dir / 'config' / 'podcast_feeds.json'),
        str(engine.base_dir / 'output' / 'audio' / 'podcasts'),
        str(engine.base_dir / 'temp'),
    )


def run_podcast_subscribe(engine, args):
    """Podcast 订阅"""
    from noteforge.sources.podcast import PodcastHandler
    podcast_config, podcast_audio, podcast_temp = _get_podcast_dirs(engine)
    ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
    try:
        info = ph.subscribe(args.podcast_subscribe, name=args.podcast_name)
        print(f"\n[OK] 已订阅: {info['name']}")
        print(f"     Feed URL: {info['feed_url']}")
        print(f"     Episodes: {info['episode_count']}")
    except Exception as e:
        print(f"\n[ERROR] 订阅失败: {e}")
        return 1
    return 0


def run_podcast_unsubscribe(engine, args):
    """Podcast 取消订阅"""
    from noteforge.sources.podcast import PodcastHandler
    podcast_config, podcast_audio, podcast_temp = _get_podcast_dirs(engine)
    ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
    try:
        ph.unsubscribe(args.podcast_unsubscribe)
        print(f"\n[OK] 已取消订阅: {args.podcast_unsubscribe}")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        return 1
    return 0


def run_podcast_list(engine, args):
    """Podcast 列表"""
    from noteforge.sources.podcast import PodcastHandler
    podcast_config, podcast_audio, podcast_temp = _get_podcast_dirs(engine)
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
    return 0


def run_podcast_sync(engine, args):
    """Podcast 同步指定 feed"""
    from noteforge.sources.podcast import PodcastHandler
    podcast_config, podcast_audio, podcast_temp = _get_podcast_dirs(engine)
    ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
    try:
        config = ph._load_feeds_config()
        if args.podcast_sync not in config['feeds']:
            print(f"\n[ERROR] 未找到订阅: {args.podcast_sync}")
            return 1
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
        return 1
    return 0


def run_podcast_sync_all(engine, args):
    """Podcast 同步所有 feeds"""
    from noteforge.sources.podcast import PodcastHandler
    podcast_config, podcast_audio, podcast_temp = _get_podcast_dirs(engine)
    ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
    config = ph._load_feeds_config()
    if not config['feeds']:
        print("\n尚未订阅任何 Podcast。")
        return 0
    print(f"\n同步 {len(config['feeds'])} 个 Podcast:")
    for slug, feed in config['feeds'].items():
        episodes = ph.list_episodes(slug, only_new=True)
        total=len(feed.get('episodes', {}))
        print(f"  {slug}: {len(episodes)}/{total} 个新 episode")
    return 0


def run_podcast_process(engine, args):
    """Podcast 下载+转写+生成笔记"""
    from noteforge.sources.podcast import PodcastHandler
    podcast_config, podcast_audio, podcast_temp = _get_podcast_dirs(engine)
    ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
    try:
        # 先同步
        config = ph._load_feeds_config()
        if args.podcast_process not in config['feeds']:
            print(f"\n[ERROR] 未找到订阅: {args.podcast_process}")
            return 1
        feed_url = config['feeds'][args.podcast_process]['feed_url']
        ph.subscribe(feed_url, name=args.podcast_process)

        # 下载新 episodes
        episodes = ph.download_new_episodes(args.podcast_process)
        if args.podcast_max > 0:
            episodes = episodes[:args.podcast_max]

        if not episodes:
            print("\n没有新 episode 需要处理。")
            return 0

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
        # Podcast 批量模式：所有笔记生成后统一触发合成
        engine.flush_pending_synthesis()
        engine.print_batch_summary(gen_results)
    except Exception as e:
        print(f"\n[ERROR] Podcast 处理失败: {e}")
        return 1
    return 0


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
