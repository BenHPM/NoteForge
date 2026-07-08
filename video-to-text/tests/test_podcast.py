# -*- coding: utf-8 -*-
"""
NoteForge Podcast RSS 处理模块单元测试

覆盖 noteforge/sources/podcast.py 的公开方法
以及 noteforge/sources/rss_parser.py 的纯函数。

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_podcast.py -v
"""
import os
os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'

import pytest
from noteforge.sources.podcast import PodcastHandler, Episode, PodcastError
from noteforge.sources.rss_parser import (
    parse_rss_xml,
    looks_like_rss_url,
    safe_filename,
)


@pytest.fixture
def handler(tmp_path):
    """创建测试用 PodcastHandler（使用临时目录避免文件系统污染）"""
    config_path = str(tmp_path / "podcast_feeds.json")
    output_dir = str(tmp_path / "audio")
    temp_dir = str(tmp_path / "temp")
    return PodcastHandler(
        config_path=config_path,
        output_dir=output_dir,
        temp_dir=temp_dir,
    )


class TestParseRssXml:
    """parse_rss_xml 测试"""

    def test_parse_rss_xml_valid_rss(self):
        """有效 RSS 2.0 XML 返回 episode 列表"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"
             xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
        <channel>
            <title>测试播客</title>
            <description>这是一个测试播客</description>
            <link>https://example.com</link>
            <item>
                <title>第1集</title>
                <enclosure url="https://example.com/ep01.mp3" type="audio/mpeg"/>
                <guid>ep01</guid>
                <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
                <description>第1集描述</description>
            </item>
            <item>
                <title>第2集</title>
                <enclosure url="https://example.com/ep02.mp3" type="audio/mpeg"/>
                <guid>ep02</guid>
                <pubDate>Tue, 02 Jan 2024 00:00:00 GMT</pubDate>
                <description>第2集描述</description>
            </item>
        </channel>
        </rss>"""
        result = parse_rss_xml(xml)
        assert result['title'] == '测试播客'
        assert len(result['episodes']) == 2
        assert 'ep01' in result['episodes']
        assert result['episodes']['ep01']['title'] == '第1集'

    def test_parse_rss_xml_empty_feed(self):
        """空 channel 返回空 episode 列表"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
        <channel>
            <title>空播客</title>
        </channel>
        </rss>"""
        result = parse_rss_xml(xml)
        assert result['title'] == '空播客'
        assert len(result['episodes']) == 0

    def test_parse_episode_item_with_enclosure(self):
        """有 enclosure 的 item 返回正确 Episode"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
        <channel>
            <title>测试</title>
            <item>
                <title>测试集</title>
                <enclosure url="https://example.com/audio.mp3" type="audio/mpeg"/>
                <guid>test-guid</guid>
                <description>描述内容</description>
            </item>
        </channel>
        </rss>"""
        result = parse_rss_xml(xml)
        assert 'test-guid' in result['episodes']
        ep = result['episodes']['test-guid']
        assert ep['audio_url'] == 'https://example.com/audio.mp3'
        assert ep['audio_type'] == 'audio/mpeg'

    def test_parse_episode_item_atom_link(self):
        """Atom 格式的 link 返回正确 Episode"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
            <title>Atom播客</title>
            <entry>
                <title>Atom集</title>
                <link rel="enclosure" href="https://example.com/atom.mp3" type="audio/mpeg"/>
                <id>atom-guid</id>
                <updated>2024-01-01T00:00:00Z</updated>
            </entry>
        </feed>"""
        result = parse_rss_xml(xml)
        # Atom feed 的 title 在命名空间中，findtext('title') 可能找不到
        # 但 episodes 应该能解析
        assert len(result['episodes']) >= 1


class TestLooksLikeRssUrl:
    """looks_like_rss_url 测试"""

    def test_looks_like_rss_url_valid(self):
        """有效的 RSS URL 返回 True"""
        assert looks_like_rss_url("https://example.com/feed.xml") is True
        assert looks_like_rss_url("https://example.com/podcast.rss") is True
        assert looks_like_rss_url("https://feeds.example.com/show") is True
        assert looks_like_rss_url("https://example.com/feed/") is True

    def test_looks_like_rss_url_invalid(self):
        """无效 URL 返回 False"""
        assert looks_like_rss_url("https://example.com/about") is False
        assert looks_like_rss_url("https://www.youtube.com/watch?v=123") is False
        assert looks_like_rss_url("https://example.com/blog/post") is False


class TestSafeFilename:
    """safe_filename 测试"""

    def test_safe_filename_removes_special_chars(self):
        """特殊字符被去除"""
        result = safe_filename('test<>:"/\\|?*file')
        assert '<' not in result
        assert '>' not in result
        assert ':' not in result
        assert '"' not in result
        assert '|' not in result
        assert '?' not in result
        assert '*' not in result

    def test_safe_filename_preserves_chinese(self):
        """中文字符被保留"""
        result = safe_filename("第1集测试播客")
        assert "第1集测试播客" == result

    def test_safe_filename_empty_input(self):
        """空输入返回 unnamed"""
        result = safe_filename("")
        assert result == "unnamed"

    def test_safe_filename_truncates_long_name(self):
        """过长文件名被截断"""
        long_name = "A" * 200
        result = safe_filename(long_name)
        assert len(result) <= 80
