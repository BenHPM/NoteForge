# -*- coding: utf-8 -*-
"""NoteForge 数据源层"""

from noteforge.sources.base import Source, SourceRegistry, FetchResult
from noteforge.sources.youtube import YouTubeHandler
from noteforge.sources.bilibili import download_bilibili, normalize_url, extract_bvid
from noteforge.sources.podcast import PodcastHandler
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
from noteforge.sources.downloader import MediaDownloader
from noteforge.sources.asr import (
    extract_audio,
    transcribe_with_paraformer,
    process_audio_file,
    process_episode,
    main as asr_main,
)
from noteforge.sources.local import LocalSource

__all__ = [
    'Source', 'SourceRegistry', 'FetchResult',
    'YouTubeHandler',
    'download_bilibili', 'normalize_url', 'extract_bvid',
    'PodcastHandler',
    'parse_rss_xml', 'parse_episode_item', 'generate_guid',
    'parse_pub_date', 'looks_like_rss_url', 'safe_filename',
    'discover_rss', 'fetch_with_retry', 'RssError',
    'MediaDownloader',
    'LocalSource',
    'extract_audio',
    'transcribe_with_paraformer',
    'process_audio_file',
    'process_episode',
    'asr_main',
]
