# -*- coding: utf-8 -*-
"""MediaDownloader 音频平台下载策略单元测试（9 tests）。"""
import os
import re
import pytest
from unittest.mock import patch, MagicMock

class TestMediaDownloader:
    """MediaDownloader 音频平台下载策略测试"""

    def test_xiaoyuzhou_url_pattern_valid(self):
        """小宇宙 URL 正则应匹配有效链接"""
        pattern = r'xiaoyuzhoufm\.com/episode/([a-f0-9]+)'
        url = "https://www.xiaoyuzhoufm.com/episode/67a3b2c1d4e5f6a7b8c9d0e1"
        m = re.search(pattern, url)
        assert m is not None
        assert m.group(1) == "67a3b2c1d4e5f6a7b8c9d0e1"

    def test_xiaoyuzhou_url_pattern_invalid(self):
        """小宇宙 URL 正则不应匹配无效链接"""
        pattern = r'xiaoyuzhoufm\.com/episode/([a-f0-9]+)'
        url = "https://www.xiaoyuzhoufm.com/podcast/abc123"
        m = re.search(pattern, url)
        assert m is None

    def test_xiaoyuzhou_url_pattern_non_hex(self):
        """小宇宙 URL 正则不应匹配非十六进制 ID"""
        pattern = r'xiaoyuzhoufm\.com/episode/([a-f0-9]+)'
        url = "https://www.xiaoyuzhoufm.com/episode/GHIJKL"
        m = re.search(pattern, url)
        assert m is None

    def test_lizhi_url_pattern_with_episode(self):
        """荔枝FM URL 正则应匹配 episode 格式"""
        pattern = r'lizhi\.fm/(?:episode/)?(\d+)'
        url = "https://www.lizhi.fm/episode/12345"
        m = re.search(pattern, url)
        assert m is not None
        assert m.group(1) == "12345"

    def test_lizhi_url_pattern_bare_id(self):
        """荔枝FM URL 正则应匹配纯数字格式"""
        pattern = r'lizhi\.fm/(?:episode/)?(\d+)'
        url = "https://www.lizhi.fm/67890"
        m = re.search(pattern, url)
        assert m is not None
        assert m.group(1) == "67890"

    def test_lizhi_url_pattern_non_numeric(self):
        """荔枝FM URL 正则不应匹配非数字"""
        pattern = r'lizhi\.fm/(?:episode/)?(\d+)'
        url = "https://www.lizhi.fm/b/12345"
        m = re.search(pattern, url)
        assert m is None

    def test_try_xiaoyuzhou_invalid_url_returns_none(self, tmp_path):
        """try_xiaoyuzhou 无效 URL 应返回 None"""
        from noteforge.sources.downloader import MediaDownloader
        result = MediaDownloader.try_xiaoyuzhou("https://example.com/not-xiaoyuzhou", str(tmp_path))
        assert result is None

    def test_try_lizhi_invalid_url_returns_none(self, tmp_path):
        """try_lizhi 无效 URL 应返回 None"""
        from noteforge.sources.downloader import MediaDownloader
        result = MediaDownloader.try_lizhi("https://example.com/not-lizhi", str(tmp_path))
        assert result is None

    def test_try_ytdlp_not_installed(self, tmp_path):
        """try_ytdlp yt-dlp 未安装时应返回 None"""
        from noteforge.sources.downloader import MediaDownloader
        with patch('shutil.which', return_value=None):
            result = MediaDownloader.try_ytdlp("https://example.com/audio", str(tmp_path))
            assert result is None
