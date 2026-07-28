# -*- coding: utf-8 -*-
"""音视频来源命令（YouTube/B站/音频平台）"""
import os
import subprocess

from noteforge.cli.commands._shared import _show_cached_quality


def run_youtube(engine, args):
    """YouTube 单视频模式"""
    try:
        from noteforge.sources.sources_factory import create_source_registry
        registry = create_source_registry(
            output_dir=str(engine.base_dir / 'output' / 'audio'),
        )
        url = args.youtube
        source = registry.match(url)
        if source is None:
            print(f"\n[ERROR] 无法识别的 YouTube URL: {url}")
            return 1

        print(f"  [数据源] {source.name}")
        metadata = source.fetch(url)
        if metadata.error:
            print(f"\n[ERROR] {metadata.error}")
            return 1

        audio_path = metadata.audio_path
        title = args.title or metadata.title
        print(f"  音频: {audio_path}")
        print(f"  标题: {title}")
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
    """YouTube 播放列表模式（通过 SourceRegistry 路由到 YouTubeSource）"""
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
    """Bilibili 视频模式（支持多 URL，通过 SourceRegistry 路由）"""
    from noteforge.sources.sources_factory import create_source_registry

    urls = args.bilibili if isinstance(args.bilibili, list) else [args.bilibili]
    registry = create_source_registry(
        output_dir=str(engine.base_dir / 'output' / 'audio'),
    )

    if len(urls) > 1:
        print(f"\n[Bilibili] 批量处理 {len(urls)} 个视频")

    gen_results = []
    errors = 0

    for i, url in enumerate(urls, 1):
        try:
            prefix = f"[{i}/{len(urls)}] " if len(urls) > 1 else ""
            print(f"\n{prefix}[Bilibili] 开始处理: {url}")

            source = registry.match(url)
            if source is None:
                print(f"\n{prefix}[ERROR] 无法识别的 URL: {url}")
                errors += 1
                continue

            metadata = source.fetch(url)
            if metadata.error:
                print(f"\n{prefix}[ERROR] {metadata.error}")
                engine.logger.error(f"Bilibili 下载失败: {metadata.error}")
                errors += 1
                continue

            audio_path = metadata.audio_path
            title = args.title or metadata.title
            method = metadata.metadata.get('method', 'source-registry')
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
        from noteforge.sources.sources_factory import create_source_registry

        input_path = args.local
        registry = create_source_registry(
            output_dir=str(engine.base_dir / 'output' / 'audio'),
        )
        source = registry.match(input_path)
        if source is None:
            print(f"\n[ERROR] 无法识别输入文件: {input_path}")
            return 1

        result = source.fetch(input_path)

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

    # --- 主流程：通过 SourceRegistry 路由 ---
    try:
        from noteforge.sources.sources_factory import create_source_registry
        url = args.audio_url

        registry = create_source_registry(output_dir=output_dir_audio)
        source = registry.match(url)
        if source is None:
            print(f"\n[ERROR] 无法识别的音频平台 URL: {url}")
            return 1

        print(f"\n  [数据源] {source.name}")
        metadata = source.fetch(url)
        if metadata.error:
            print(f"\n[ERROR] {metadata.error}")
            engine.logger.error(f"音频平台下载失败: {metadata.error}")
            return 1

        title = args.title or metadata.title
        engine.logger.info(f"音频平台: 下载完成 {title}")
        print(f"  音频: {metadata.audio_path}")
        print(f"  标题: {title}")
        result = engine.generate_note(
            metadata.audio_path, title=title,
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
