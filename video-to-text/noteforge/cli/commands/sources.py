# -*- coding: utf-8 -*-
"""音视频来源命令（YouTube/B站/音频平台）"""
import os
import subprocess

from noteforge.cli.commands._shared import _show_cached_quality


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


def run_local(engine, args):
    """本地音频/视频文件模式"""
    try:
        from noteforge.sources.local import LocalSource
        from noteforge.sources.base import SourceRegistry

        input_path = args.local
        registry = SourceRegistry()
        registry.register(LocalSource())
        result = registry.fetch(input_path)

        if result.error:
            print(f"\n[ERROR] {result.error}")
            engine.logger.error(f"本地文件处理失败: {result.error}")
            return 1

        print(f"  音频: {result.audio_path}")
        print(f"  标题: {result.title}")
        gen_result = engine.generate_note(
            result.audio_path, title=args.title or result.title,
            provider_override=args.provider, force=args.force,
            mode=args.mode,
            with_context=args.with_context,
            context_limit=args.context_limit,
        )
        # 单文件模式：立即触发待合成域的合成
        engine.flush_pending_synthesis()
        if gen_result.error:
            if '已存在' in gen_result.error:
                _show_cached_quality(engine, gen_result.note_path)
            else:
                print(f"\n[ERROR] {gen_result.error}")
                return 1
    except ValueError as e:
        print(f"\n[ERROR] 无法识别输入文件: {e}")
        return 1
    except Exception as e:
        print(f"\n[ERROR] 本地文件处理失败: {e}")
        engine.logger.error(f"本地文件处理异常: {e}", exc_info=True)
        return 1
    return 0


def run_audio_url(engine, args):
    """音频平台链接模式（小宇宙/喜马拉雅/荔枝FM 等）"""
    output_dir_audio = str(engine.base_dir / 'output' / 'audio')
    os.makedirs(output_dir_audio, exist_ok=True)

    # --- 主流程：降级链 ---
    try:
        from noteforge.sources.downloader import MediaDownloader
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
