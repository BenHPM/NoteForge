# -*- coding: utf-8 -*-
"""NoteForge 数据源层

注意：不在此 re-export asr 函数，以避免子进程调用 ASR 时
runpy 的模块缓存问题（noteforge.sources.asr 已在 sys.modules 中时
__name__ != "__main__"，main() 守卫失效 → exit=1 静默失败）。
直接 import from noteforge.sources.asr 即可。
"""

from noteforge.sources.base import Source, SourceRegistry, FetchResult
from noteforge.sources.sources_factory import create_source_registry
from noteforge.sources.youtube import YouTubeSource, YouTubeHandler
from noteforge.sources.bilibili import BilibiliSource, download_bilibili, normalize_url, extract_bvid
from noteforge.sources.podcast import PodcastHandler, PodcastSource
from noteforge.sources.rss_parser import (
    parse_rss_xml,
    parse_episode_item,
    generate_guid,
    parse_pub_date,
    looks_like_rss_url,
    safe_filename,
    discover_rss,
    fetch_with_retry,
    RssError,
)
from noteforge.sources.downloader import (
    MediaDownloader, AudioPlatformSource,
    _run_ytdlp_download,
)
# 注意：不在此导入 noteforge.sources.asr（见模块注释）
from noteforge.sources.local import LocalSource
from noteforge.sources.asr_provider import (
    ASRProvider, TranscriptionResult, ASRTimeoutError,
    LocalParaformerASR, MockASR,
)

__all__ = [
    'Source', 'SourceRegistry', 'FetchResult', 'create_source_registry',
    'YouTubeSource', 'YouTubeHandler',
    'BilibiliSource', 'download_bilibili', 'normalize_url', 'extract_bvid',
    'PodcastHandler', 'PodcastSource',
    'parse_rss_xml', 'parse_episode_item', 'generate_guid',
    'parse_pub_date', 'looks_like_rss_url', 'safe_filename',
    'discover_rss', 'fetch_with_retry', 'RssError',
    'MediaDownloader', 'AudioPlatformSource', '_run_ytdlp_download',
    'LocalSource',
    'ASRProvider', 'TranscriptionResult', 'ASRTimeoutError',
    'LocalParaformerASR', 'MockASR',
]
