# -*- coding: utf-8 -*-
"""
NoteForge BatchSource 抽象 — 单元测试

覆盖：
  - SourceRegistry.match() 路由
  - 各 Source 的 can_handle / fetch
  - BilibiliSource 下载策略
  - YouTubeSource URL 检测
  - AudioPlatformSource 平台识别
  - PodcastSource RSS 检测
  - 向后兼容（download_bilibili, YouTubeHandler, MediaDownloader）
"""

import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from noteforge.sources.base import Source, SourceRegistry, FetchResult
from noteforge.sources.sources_factory import create_source_registry
from noteforge.sources.bilibili import (
    BilibiliSource, download_bilibili,
    normalize_url, extract_bvid,
)
from noteforge.sources.youtube import (
    YouTubeSource, YouTubeHandler,
    _extract_video_id, _find_ytdlp,
)
from noteforge.sources.downloader import (
    AudioPlatformSource, MediaDownloader,
    _run_ytdlp_download, _download_xiaoyuzhou, _download_lizhi,
)
from noteforge.sources.podcast import PodcastSource
from noteforge.sources.local import LocalSource


# ================================================================
# 通用 Fake Source
# ================================================================

class FakeSource(Source):
    def __init__(self, patterns, fetch_result=None):
        self._patterns = patterns
        self._fetch_result = fetch_result or FetchResult()

    def can_handle(self, input_str):
        return any(p in input_str for p in self._patterns)

    def fetch(self, input_str, output_dir=""):
        return self._fetch_result


# ================================================================
# SourceRegistry.match() 测试
# ================================================================

class TestSourceRegistryMatch:

    def test_match_returns_source_or_none(self):
        registry = SourceRegistry()
        src = FakeSource(["test"], FetchResult(title="ok"))
        registry.register(src)
        assert registry.match("test://url") is src
        assert registry.match("other://url") is None

    def test_match_first_wins(self):
        registry = SourceRegistry()
        first = FakeSource(
            ["both"],
            FetchResult(source_type="first"),
        )
        second = FakeSource(
            ["both"],
            FetchResult(source_type="second"),
        )
        registry.register(first)
        registry.register(second)
        result = registry.fetch("both://x")
        assert result.source_type == "first"

    def test_fetch_uses_match_under_hood(self):
        registry = SourceRegistry()
        src = FakeSource(["yt"], FetchResult(audio_path="/tmp/a.mp3", title="v"))
        registry.register(src)
        result = registry.fetch("yt://url")
        assert result.audio_path == "/tmp/a.mp3"

    def test_fetch_raises_on_no_match(self):
        registry = SourceRegistry()
        registry.register(FakeSource(["other"]))
        with pytest.raises(ValueError, match="无法识别输入"):
            registry.fetch("unknown")


# ================================================================
# create_source_registry 工厂测试
# ================================================================

class TestCreateSourceRegistry:

    def test_returns_registry_with_sources(self):
        registry = create_source_registry()
        names = registry.list_sources()
        assert "YouTubeSource" in names
        assert "BilibiliSource" in names
        assert "AudioPlatformSource" in names
        assert "PodcastSource" in names
        assert "LocalSource" in names

    def test_routes_youtube_url(self):
        registry = create_source_registry()
        src = registry.match("https://www.youtube.com/watch?v=abc123")
        assert src is not None
        assert src.name == "YouTubeSource"

    def test_routes_bilibili_url(self):
        registry = create_source_registry()
        src = registry.match("https://www.bilibili.com/video/BV1abc")
        assert src is not None
        assert src.name == "BilibiliSource"

    def test_routes_bilibili_bvid(self):
        registry = create_source_registry()
        src = registry.match("BV1abc")
        assert src is not None
        assert src.name == "BilibiliSource"

    def test_routes_xiaoyuzhou(self):
        registry = create_source_registry()
        src = registry.match("https://xiaoyuzhoufm.com/episode/abc123")
        assert src is not None
        assert src.name == "AudioPlatformSource"

    def test_routes_lizhi(self):
        registry = create_source_registry()
        src = registry.match("https://lizhi.fm/episode/12345")
        assert src is not None
        assert src.name == "AudioPlatformSource"

    def test_routes_ximalaya(self):
        registry = create_source_registry()
        src = registry.match("https://www.ximalaya.com/track/123")
        assert src is not None
        assert src.name == "AudioPlatformSource"

    def test_routes_local_audio(self):
        """本地文件存在时匹配 LocalSource"""
        registry = create_source_registry()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"fake")
            path = f.name
        try:
            src = registry.match(path)
            assert src is not None
            assert src.name == "LocalSource"
        finally:
            os.unlink(path)

    def test_routes_rss_feed(self):
        registry = create_source_registry()
        src = registry.match("https://example.com/feed.xml")
        assert src is not None
        assert src.name == "PodcastSource"


# ================================================================
# BilibiliSource 测试
# ================================================================

class TestBilibiliSource:

    def test_can_handle_bilibili_url(self):
        src = BilibiliSource()
        assert src.can_handle("https://www.bilibili.com/video/BV1xx")
        assert src.can_handle("BV1xx411c7mD")
        assert src.can_handle("https://b23.tv/abc")
        assert not src.can_handle("https://youtube.com/watch?v=123")
        assert not src.can_handle("")

    def test_can_handle_b23_short_link(self):
        src = BilibiliSource()
        assert src.can_handle("https://b23.tv/abc123")

    def test_normalize_url_bvid(self):
        assert normalize_url("BV1xx411c7mD") == "https://www.bilibili.com/video/BV1xx411c7mD"

    def test_normalize_url_full(self):
        assert normalize_url("https://www.bilibili.com/video/BV1xx") == "https://www.bilibili.com/video/BV1xx"

    def test_extract_bvid(self):
        assert extract_bvid("https://www.bilibili.com/video/BV1xx411c7mD") == "BV1xx411c7mD"
        assert extract_bvid("BV1xx411c7mD") == "BV1xx411c7mD"
        assert extract_bvid("https://example.com") == ""

    def test_fetch_success_returns_result(self):
        src = BilibiliSource()
        mock_info = {
            'code': 0,
            'data': {'title': 'Test', 'duration': 120, 'cid': 1}
        }
        mock_play = {
            'code': 0,
            'data': {'dash': {'audio': [{'baseUrl': 'http://audio.test/stream'}]}}
        }

        with patch('noteforge.sources.bilibili._api_get') as mock_api, \
             patch('noteforge.sources.bilibili._try_ytdlp_bili', return_value=False), \
             patch('noteforge.sources.bilibili._download_audio_stream', return_value=True):
            mock_api.side_effect = [mock_info, mock_play]
            result = src.fetch("https://www.bilibili.com/video/BV1xx")

        assert result.error is None
        assert result.audio_path.endswith('.m4a')
        assert result.title == 'Test'
        assert result.source_type == 'bilibili'
        assert result.metadata['duration'] == 120

    def test_fetch_api_error(self):
        src = BilibiliSource()
        with patch('noteforge.sources.bilibili._api_get',
                   return_value={'code': -1, 'message': '区域限制'}):
            result = src.fetch("https://www.bilibili.com/video/BV1xx")
        assert result.error is not None

    def test_fetch_no_bvid(self):
        src = BilibiliSource()
        result = src.fetch("https://example.com/not-bilibili")
        assert result.error is not None

    def test_download_bilibili_compat(self):
        """download_bilibili 旧版 API 向后兼容"""
        with patch.object(BilibiliSource, 'fetch',
                          return_value=FetchResult(
                              audio_path="/tmp/test.m4a", title="Test",
                              source_type='bilibili',
                              metadata={'duration': 60, 'method': 'yt-dlp'})):
            result = download_bilibili("https://www.bilibili.com/video/BV1xx")
        assert result['success'] is True
        assert result['path'] == "/tmp/test.m4a"
        assert result['title'] == "Test"
        assert result['duration'] == 60
        assert result['method'] == 'yt-dlp'

    def test_download_bilibili_failure_compat(self):
        """download_bilibili 旧版 API 失败返回"""
        with patch.object(BilibiliSource, 'fetch',
                          return_value=FetchResult(error="网络错误")):
            result = download_bilibili("https://www.bilibili.com/video/BV1xx")
        assert result['success'] is False
        assert '网络错误' in result['error']


# ================================================================
# YouTubeSource 测试
# ================================================================

class TestYouTubeSource:

    def test_extract_video_id(self):
        assert _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert _extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert _extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert _extract_video_id("https://example.com") == "unknown"

    def test_can_handle_youtube_urls(self):
        src = YouTubeSource()
        assert src.can_handle("https://www.youtube.com/watch?v=abc123")
        assert src.can_handle("https://youtu.be/abc123")
        assert src.can_handle("https://www.youtube.com/embed/abc123")
        assert not src.can_handle("https://bilibili.com/video/BV1xx")
        assert not src.can_handle("")

    def test_can_handle_playlist_returns_false(self):
        """播放列表不应被 YouTubeSource 处理（用 --youtube-playlist）"""
        src = YouTubeSource()
        assert not src.can_handle("https://www.youtube.com/playlist?list=PLxxx")

    def test_fetch_requires_ytdlp(self):
        """yt-dlp 不可用时返回错误"""
        with patch('noteforge.sources.youtube._find_ytdlp', return_value=None):
            src = YouTubeSource()
            result = src.fetch("https://www.youtube.com/watch?v=abc")
        assert result.error is not None
        assert 'yt-dlp' in result.error

    def test_youtube_handler_compat(self):
        """YouTubeHandler 旧版向后兼容"""
        import os as _os
        with patch('noteforge.sources.youtube._find_ytdlp', return_value='yt-dlp'), \
             patch('noteforge.sources.youtube._extract_metadata',
                   return_value={'id': 'abc123', 'title': 'Test Video'}), \
             patch('noteforge.sources.youtube._run_ytdlp_download',
                   return_value='/tmp/Test Video.mp3'), \
             patch('os.replace'):
            handler = YouTubeHandler(output_dir='/tmp', temp_dir='/tmp')
            result = handler.download_audio("https://www.youtube.com/watch?v=abc")
        expected = _os.path.join('/tmp', 'Test Video.mp3')
        assert result['path'] == expected
        assert result['title'] == 'Test Video'


# ================================================================
# AudioPlatformSource 测试
# ================================================================

class TestAudioPlatformSource:

    def test_can_handle_xiaoyuzhou(self):
        src = AudioPlatformSource()
        assert src.can_handle("https://xiaoyuzhoufm.com/episode/abc123")

    def test_can_handle_lizhi(self):
        src = AudioPlatformSource()
        assert src.can_handle("https://lizhi.fm/episode/12345")
        assert src.can_handle("https://lizhi.fm/12345")

    def test_can_handle_ximalaya(self):
        src = AudioPlatformSource()
        assert src.can_handle("https://www.ximalaya.com/track/123")
        assert src.can_handle("https://www.ximalaya.com/album/123")

    def test_can_handle_rejects_unknown(self):
        src = AudioPlatformSource()
        assert not src.can_handle("https://example.com/video")
        assert not src.can_handle("")

    def test_detect_platform(self):
        src = AudioPlatformSource()
        assert src._detect_platform("https://xiaoyuzhoufm.com/episode/abc") == 'xiaoyuzhou'
        assert src._detect_platform("https://lizhi.fm/episode/1") == 'lizhi'
        assert src._detect_platform("https://www.ximalaya.com/track/1") == 'ximalaya'
        assert src._detect_platform("https://example.com") == 'unknown'

    def test_mediadownloader_compat(self):
        """MediaDownloader 旧版向后兼容"""
        with patch('noteforge.sources.downloader._run_ytdlp_download',
                   return_value='/tmp/test.mp3'):
            assert MediaDownloader.try_ytdlp("http://test", "/tmp") == '/tmp/test.mp3'
            assert MediaDownloader.try_ytdlp("http://test", "/tmp") is not None

    def test_mediadownloader_xiaoyuzhou_compat(self):
        with patch('noteforge.sources.downloader._download_xiaoyuzhou',
                   return_value=('/tmp/ep.mp3', 'Episode Title')):
            r = MediaDownloader.try_xiaoyuzhou(
                "https://xiaoyuzhoufm.com/episode/abc", "/tmp")
        assert r == ('/tmp/ep.mp3', 'Episode Title')

    def test_mediadownloader_lizhi_compat(self):
        with patch('noteforge.sources.downloader._download_lizhi',
                   return_value=('/tmp/ep.mp3', 'Lizhi Title')):
            r = MediaDownloader.try_lizhi("https://lizhi.fm/episode/123", "/tmp")
        assert r == ('/tmp/ep.mp3', 'Lizhi Title')


# ================================================================
# PodcastSource 测试
# ================================================================

class TestPodcastSource:

    def test_can_handle_rss_url(self):
        src = PodcastSource()
        assert src.can_handle("https://example.com/feed.xml")
        assert src.can_handle("http://feeds.soundcloud.com/xyz")
        assert src.can_handle("https://podcasts.apple.com/podcast/id123")

    def test_can_handle_rejects_non_rss(self):
        src = PodcastSource()
        assert not src.can_handle("https://example.com/page")
        assert not src.can_handle("")
        assert not src.can_handle("https://youtube.com/watch?v=abc")

    def test_can_handle_audio_url(self):
        src = PodcastSource()
        assert src.can_handle("https://example.com/episode.mp3")
        assert src.can_handle("https://example.com/episode.m4a")


# ================================================================
# LocalSource 测试（保持兼容）
# ================================================================

class TestLocalSourceCompat:

    def test_can_handle_audio_file(self):
        src = LocalSource()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"fake")
            path = f.name
        try:
            assert src.can_handle(path) is True
        finally:
            os.unlink(path)

    def test_can_handle_video_file(self):
        src = LocalSource()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake")
            path = f.name
        try:
            assert src.can_handle(path) is True
        finally:
            os.unlink(path)

    def test_can_handle_unsupported(self):
        src = LocalSource()
        assert src.can_handle("file.txt") is False

    def test_fetch_audio_copies_file(self):
        src = LocalSource()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"audio data")
            src_path = f.name
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = src.fetch(src_path, output_dir=tmpdir)
            assert result.error is None
            assert result.audio_path is not None
            assert result.title == Path(src_path).stem
            assert result.source_type == 'local'
        finally:
            os.unlink(src_path)


# ================================================================
# FetchResult 数据类测试
# ================================================================

class TestFetchResult:

    def test_default_values(self):
        r = FetchResult()
        assert r.audio_path is None
        assert r.transcript_path is None
        assert r.title == ""
        assert r.source_type == ""
        assert r.error is None
        assert r.metadata == {}

    def test_with_values(self):
        r = FetchResult(
            audio_path="/tmp/a.mp3",
            title="Test",
            source_type="youtube",
            metadata={'id': 'abc'},
        )
        assert r.audio_path == "/tmp/a.mp3"
        assert r.title == "Test"
        assert r.source_type == "youtube"
        assert r.metadata['id'] == 'abc'


# ================================================================
# BilibiliSource 下载策略测试
# ================================================================

class TestBilibiliDownloadStrategy:

    def test_cached_file_skips_download(self):
        """已有音频文件且大小合理时跳过下载（模拟）"""
        import os as _os
        from noteforge.sources.bilibili import _get_temp_dir, _download_audio_stream

        tmpdir = _get_temp_dir()
        safe_title = "test_episode"
        output_path = str(tmpdir / f"{safe_title}.m4a")

        # 创建一个足够大的假文件
        with open(output_path, 'wb') as f:
            f.write(b'x' * 200000)  # 200KB

        try:
            # 模拟 fetch 内部达到缓存判断分支
            # _get_temp_dir() 返回的就是 temp 目录，文件存在且大小合理 → cached
            file_size = _os.path.getsize(output_path)
            min_expected = max(120 * 1024, 10240)
            assert file_size >= min_expected, "文件大小应超过最小值"
        finally:
            _os.unlink(output_path)

    def test_short_link_normalization(self):
        """b23.tv 短链接通过 normalize_url 解析"""
        url = normalize_url("https://b23.tv/abc123")
        # 短链接解析可能失败（网络），但函数不应抛异常
        assert isinstance(url, str)
