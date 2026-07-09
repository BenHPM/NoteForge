# -*- coding: utf-8 -*-
"""Podcast 订阅与管理命令"""
from noteforge.cli.commands._shared import _show_cached_quality


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
        total = len(feed.get('episodes', {}))
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
