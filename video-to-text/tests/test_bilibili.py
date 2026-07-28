"""
bilibili_download 模块单元测试

覆盖：
  - URL 标准化（BV 号补齐、完整 URL 不变）
  - BV 号提取
  - 无效 URL 返回
  - 错误返回格式（dict + error key）

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_bilibili.py -v
"""
import os
import pytest

class TestBilibiliDownload:
    """bilibili_download 模块测试"""

    def test_normalize_url_bvid(self):
        from noteforge.sources.bilibili import normalize_url
        result = normalize_url("BV1xx411c7mD")
        assert result == "https://www.bilibili.com/video/BV1xx411c7mD"

    def test_normalize_url_full_url(self):
        from noteforge.sources.bilibili import normalize_url
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        assert normalize_url(url) == url

    def test_extract_bvid(self):
        from noteforge.sources.bilibili import extract_bvid
        assert extract_bvid("https://www.bilibili.com/video/BV1xx411c7mD") == "BV1xx411c7mD"
        assert extract_bvid("BV1abc123") == "BV1abc123"
        assert extract_bvid("https://example.com") == ""

    def test_download_bilibili_invalid_url(self):
        from noteforge.sources.bilibili import download_bilibili
        result = download_bilibili("https://example.com/not-bilibili")
        assert result["success"] is False
        assert "error" in result

    def test_download_bilibili_error_dict_format(self):
        """验证 line 167 bug 修复：错误返回必须是标准 dict 格式"""
        from noteforge.sources.bilibili import download_bilibili
        # 使用不存在的 BV 号，触发 get_video_info 失败
        result = download_bilibili("BV000000000000000000000")
        assert isinstance(result, dict)
        assert "success" in result
        assert result["success"] is False
        # 确保 error key 是字符串，不是 f-string key 语法错误
        assert isinstance(result.get("error", ""), str)
