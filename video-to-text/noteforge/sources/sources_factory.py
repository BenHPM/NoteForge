# -*- coding: utf-8 -*-
"""
NoteForge 数据源工厂

提供预配置的 SourceRegistry，所有 Source 实现在此统一注册。
新增数据源只需：1) 实现 Source 子类  2) 在此 register
"""

from noteforge.sources.base import SourceRegistry
from noteforge.sources.youtube import YouTubeSource
from noteforge.sources.bilibili import BilibiliSource
from noteforge.sources.downloader import AudioPlatformSource
from noteforge.sources.podcast import PodcastSource
from noteforge.sources.local import LocalSource


def create_source_registry(output_dir: str = "") -> SourceRegistry:
    """创建并返回预配置的 SourceRegistry（按优先级注册）

    Args:
        output_dir: 音频下载输出目录（传给各 Source 的 fetch）

    Returns:
        已注册所有数据源的 SourceRegistry
    """
    registry = SourceRegistry()
    registry.register(YouTubeSource())
    registry.register(BilibiliSource())
    registry.register(AudioPlatformSource())
    registry.register(PodcastSource())
    registry.register(LocalSource())  # 本地文件放最后（最宽松的匹配）
    return registry
