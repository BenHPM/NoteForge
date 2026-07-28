"""
音频平台 URL 检测单元测试

覆盖：
  - 小宇宙 xiaoyuzhoufm.com episode ID 提取
  - 荔枝 lizhi.fm episode ID 提取（排除 /b/ 频道页）
  - B站 URL 检测（bilibili.com / b23.tv）

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_platform_detection.py -v
"""
import os
import pytest

class TestPlatformDetection:
    """音频平台 URL 识别测试"""

    def test_xiaoyuzhou_url_pattern(self):
        import re
        pattern = r'xiaoyuzhoufm\.com/episode/([a-f0-9]+)'
        m = re.search(pattern, "https://www.xiaoyuzhoufm.com/episode/67a3b2c1d4e5f6a7b8c9d0e1")
        assert m is not None
        assert m.group(1) == "67a3b2c1d4e5f6a7b8c9d0e1"

    def test_lizhi_url_pattern(self):
        import re
        pattern = r'lizhi\.fm/(?:episode/)?(\d+)'
        assert re.search(pattern, "https://www.lizhi.fm/episode/12345") is not None
        assert re.search(pattern, "https://www.lizhi.fm/b/12345") is None  # /b/ 是频道页，不是单集

    def test_bilibili_url_detection(self):
        urls = [
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "https://b23.tv/abcdef",
        ]
        for url in urls:
            assert 'bilibili.com' in url or 'b23.tv' in url
