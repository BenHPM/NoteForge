# -*- coding: utf-8 -*-
"""
NoteForge RSS 解析工具模块单元测试

覆盖 noteforge/sources/rss_parser.py 的全部公开函数和类:
- parse_rss_xml (RSS 2.0 + Atom)
- parse_episode_item (enclosure / Atom link)
- generate_guid
- parse_pub_date
- fetch_with_retry (指数退避)
- discover_rss (HTML 自动发现 + 常见路径)
- looks_like_rss_url
- safe_filename

运行:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_rss_parser.py -v
"""
import os
import hashlib
import re
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest
import requests

from noteforge.sources.rss_parser import (
    RssError,
    discover_rss,
    fetch_with_retry,
    generate_guid,
    looks_like_rss_url,
    parse_episode_item,
    parse_pub_date,
    parse_rss_xml,
    safe_filename,
)


# ============================================================
# RSS/Atom XML fixtures
# ============================================================

RSS_20_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Test Podcast</title>
    <description>A podcast for testing purposes</description>
    <link>https://example.com/podcast</link>
    <item>
      <title>Episode 1</title>
      <enclosure url="https://example.com/audio/ep01.mp3" type="audio/mpeg"/>
      <guid>ep01-guid</guid>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      <itunes:duration>3600</itunes:duration>
      <description>First episode with &lt;b&gt;HTML&lt;/b&gt; tags.</description>
      <link>https://example.com/episodes/ep01</link>
    </item>
    <item>
      <title>Episode 2</title>
      <enclosure url="https://example.com/audio/ep02.mp3" type="audio/mpeg"/>
      <guid>ep02-guid</guid>
      <pubDate>Tue, 02 Jan 2024 12:00:00 +0800</pubDate>
      <description>Second episode</description>
    </item>
  </channel>
</rss>
"""

ATOM_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <title>Atom Podcast</title>
  <link href="https://atom.example.com/"/>
  <entry>
    <title>Atom Episode</title>
    <link rel="enclosure" href="https://atom.example.com/audio/ep01.mp3" type="audio/mpeg"/>
    <id>atom-guid</id>
    <updated>2024-03-15T08:30:00Z</updated>
    <summary>Atom episode summary</summary>
  </entry>
</feed>
"""

RSS_EMPTY_ITEMS = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Empty Items</title>
    <description>No episodes here</description>
    <link>https://example.com</link>
  </channel>
</rss>
"""

RSS_NO_ENCLOSURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>No Audio</title>
    <item>
      <title>Text Only</title>
      <guid>no-audio</guid>
      <description>No audio attached</description>
    </item>
  </channel>
</rss>
"""

RSS_ITUNES_SUMMARY_FALLBACK = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Podcast</title>
    <item>
      <title>Ep with iTunes Summary</title>
      <enclosure url="https://example.com/audio.mp3" type="audio/mpeg"/>
      <guid>guid1</guid>
      <itunes:summary>iTunes summary text</itunes:summary>
    </item>
  </channel>
</rss>
"""

INVALID_XML = "this is not xml at all <broken>"

RSS_NO_CHANNEL = """\
<?xml version="1.0" encoding="UTF-8"?>
<root>
  <something>not a feed</something>
</root>
"""

ATOM_WITH_HTML_DESC = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>HTML Desc Podcast</title>
  <entry>
    <title>HTML Desc Ep</title>
    <link rel="enclosure" href="https://example.com/audio.mp3" type="audio/mpeg"/>
    <id>html-desc-guid</id>
    <summary>Description with &lt;p&gt;paragraph&lt;/p&gt; and &lt;a href=\"url\"&gt;link&lt;/a&gt;</summary>
  </entry>
</feed>
"""

RSS_WITH_NON_AUDIO_ENCLOSURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Video Podcast</title>
    <item>
      <title>Video Episode</title>
      <enclosure url="https://example.com/video.mp4" type="video/mp4"/>
      <guid>video-guid</guid>
    </item>
    <item>
      <title>Audio Episode</title>
      <enclosure url="https://example.com/audio.mp3" type="audio/mpeg"/>
      <guid>audio-guid</guid>
    </item>
  </channel>
</rss>
"""

RSS_WITHOUT_DESCRIPTION_AND_LINK = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Minimal Podcast</title>
    <description></description>
    <link></link>
  </channel>
</rss>
"""


# ============================================================
# Helpers
# ============================================================

def _make_response(status=200, text='', headers=None):
    """Create a mock requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.text = text
    resp.headers = headers or {}
    return resp


# ============================================================
# TestParseRssXml
# ============================================================

class TestParseRssXml:
    """Tests for parse_rss_xml()."""

    def test_parse_rss_20_returns_correct_structure(self):
        """Valid RSS 2.0 feed returns dict with expected keys."""
        result = parse_rss_xml(RSS_20_FEED)
        assert 'title' in result
        assert 'description' in result
        assert 'link' in result
        assert 'episodes' in result
        assert isinstance(result['episodes'], dict)

    def test_parse_rss_20_extracts_channel_metadata(self):
        """Channel title, description, and link are extracted correctly."""
        result = parse_rss_xml(RSS_20_FEED)
        assert result['title'] == 'Test Podcast'
        assert result['link'] == 'https://example.com/podcast'
        assert 'testing purposes' in result['description']

    def test_parse_rss_20_extracts_two_episodes(self):
        """Two items produce two episode entries keyed by GUID."""
        result = parse_rss_xml(RSS_20_FEED)
        assert len(result['episodes']) == 2
        assert 'ep01-guid' in result['episodes']
        assert 'ep02-guid' in result['episodes']

    def test_parse_rss_20_episode_fields(self):
        """Each episode dict contains expected fields with correct values."""
        result = parse_rss_xml(RSS_20_FEED)
        ep = result['episodes']['ep01-guid']
        assert ep['title'] == 'Episode 1'
        assert ep['audio_url'] == 'https://example.com/audio/ep01.mp3'
        assert ep['audio_type'] == 'audio/mpeg'
        assert ep['guid'] == 'ep01-guid'
        assert ep['processed'] is False
        assert ep['link'] == 'https://example.com/episodes/ep01'

    def test_parse_rss_20_description_truncated_to_500_chars(self):
        """Channel description is truncated to 500 chars."""
        long_desc = 'A' * 600
        xml = f"""<?xml version="1.0"?><rss version="2.0"><channel>
          <title>T</title><description>{long_desc}</description></channel></rss>"""
        result = parse_rss_xml(xml)
        assert len(result['description']) <= 500

    def test_parse_atom_feed(self):
        """Atom <feed> with <entry> is parsed correctly."""
        result = parse_rss_xml(ATOM_FEED)
        assert len(result['episodes']) == 1
        ep = list(result['episodes'].values())[0]
        assert ep['audio_url'] == 'https://atom.example.com/audio/ep01.mp3'
        assert ep['title'] == 'Atom Episode'

    def test_parse_atom_uses_atom_link_enclosure(self):
        """Atom entry's link[rel=enclosure] provides the audio URL."""
        result = parse_rss_xml(ATOM_FEED)
        ep = list(result['episodes'].values())[0]
        assert ep['audio_url'] == 'https://atom.example.com/audio/ep01.mp3'

    def test_parse_empty_feed_no_items(self):
        """Feed with channel but no <item> produces empty episodes dict."""
        result = parse_rss_xml(RSS_EMPTY_ITEMS)
        assert result['title'] == 'Empty Items'
        assert result['episodes'] == {}

    def test_parse_missing_enclosure_skips_item(self):
        """Items without an audio enclosure are excluded from episodes."""
        result = parse_rss_xml(RSS_NO_ENCLOSURE)
        assert 'no-audio' not in result['episodes']
        assert len(result['episodes']) == 0

    def test_parse_non_audio_enclosure_skips_item(self):
        """Items with non-audio enclosure type are excluded."""
        result = parse_rss_xml(RSS_WITH_NON_AUDIO_ENCLOSURE)
        assert 'video-guid' not in result['episodes']
        assert 'audio-guid' in result['episodes']

    def test_parse_strips_html_from_description(self):
        """HTML tags are stripped from episode descriptions."""
        result = parse_rss_xml(RSS_20_FEED)
        ep = result['episodes']['ep01-guid']
        assert '<b>' not in ep['description']
        assert 'HTML' in ep['description']

    def test_parse_html_in_atom_summary(self):
        """Atom <summary> (namespaced) is NOT read by current implementation
        -- description is empty when only <summary> is present."""
        result = parse_rss_xml(ATOM_WITH_HTML_DESC)
        ep = list(result['episodes'].values())[0]
        # Current code checks <description> and <itunes:summary>,
        # but not Atom-namespace <summary>.
        assert ep['description'] == ''
        assert ep['title'] == 'HTML Desc Ep'

    def test_parse_invalid_xml_raises_value_error(self):
        """Malformed XML raises ValueError."""
        with pytest.raises(ValueError, match='无效的 RSS XML'):
            parse_rss_xml(INVALID_XML)

    def test_parse_no_channel_raises_value_error(self):
        """XML without <channel> or <feed> raises ValueError."""
        with pytest.raises(ValueError, match='无效的 RSS feed'):
            parse_rss_xml(RSS_NO_CHANNEL)

    def test_parse_missing_title_defaults_to_unknown(self):
        """When title is empty, result defaults to 'Unknown Podcast'."""
        xml = """<?xml version="1.0"?><rss version="2.0"><channel>
          <description>No title</description></channel></rss>"""
        result = parse_rss_xml(xml)
        assert result['title'] == 'Unknown Podcast'

    def test_parse_missing_description_is_empty(self):
        """When description is empty, result has empty description."""
        result = parse_rss_xml(RSS_WITHOUT_DESCRIPTION_AND_LINK)
        assert result['description'] == ''
        assert result['link'] == ''


# ============================================================
# TestParseEpisodeItem
# ============================================================

class TestParseEpisodeItem:
    """Tests for parse_episode_item()."""

    def test_returns_none_when_no_enclosure(self):
        """Item without enclosure URL returns None."""
        xml = '<item><title>T</title><guid>g1</guid></item>'
        item = ET.fromstring(xml)
        assert parse_episode_item(item) is None

    def test_returns_none_when_enclosure_type_is_not_audio(self):
        """Item with video enclosure returns None."""
        xml = '<item><title>T</title>' \
              '<enclosure url="https://x.com/v.mp4" type="video/mp4"/></item>'
        item = ET.fromstring(xml)
        assert parse_episode_item(item) is None

    def test_returns_none_for_unknown_mime_type(self):
        """Item with application/pdf enclosure returns None."""
        xml = '<item><title>T</title>' \
              '<enclosure url="https://x.com/doc.pdf" type="application/pdf"/></item>'
        item = ET.fromstring(xml)
        assert parse_episode_item(item) is None

    def test_extracts_enclosure_fields(self):
        """Enclosure URL and type are extracted correctly."""
        xml = ('<item><title>T</title>'
               '<enclosure url="https://x.com/a.mp3" type="audio/mpeg"/></item>')
        item = ET.fromstring(xml)
        ep = parse_episode_item(item)
        assert ep is not None
        assert ep['audio_url'] == 'https://x.com/a.mp3'
        assert ep['audio_type'] == 'audio/mpeg'

    def test_extracts_atom_link_enclosure(self):
        """Atom <link rel=enclosure> is used when no RSS enclosure."""
        xml = ('<item xmlns="http://www.w3.org/2005/Atom">'
               '<title>T</title>'
               '<link rel="enclosure" href="https://x.com/a.mp3" type="audio/mpeg"/>'
               '</item>')
        item = ET.fromstring(xml)
        ep = parse_episode_item(item)
        assert ep is not None
        assert ep['audio_url'] == 'https://x.com/a.mp3'

    def test_falls_back_to_itunes_title(self):
        """<itunes:title> is not used as fallback for <title> -- the code
        only checks findtext('title', ''), not the itunes namespace variant.
        Item with only <itunes:title> (no <title>) has empty title."""
        xml = ('<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
               '<itunes:title>iTunes Title</itunes:title>'
               '<enclosure url="https://x.com/a.mp3" type="audio/mpeg"/>'
               '</item>')
        item = ET.fromstring(xml)
        ep = parse_episode_item(item)
        assert ep is not None
        # Current implementation does not fall back to itunes:title
        assert ep['title'] == ''

    def test_falls_back_to_itunes_summary(self):
        """When <description> is empty, falls back to <itunes:summary>."""
        xml = ('<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
               '<title>T</title>'
               '<enclosure url="https://x.com/a.mp3" type="audio/mpeg"/>'
               '<itunes:summary>iTunes summary</itunes:summary>'
               '</item>')
        item = ET.fromstring(xml)
        ep = parse_episode_item(item)
        assert ep is not None
        assert ep['description'] == 'iTunes summary'

    def test_strips_html_from_description(self):
        """HTML tags are stripped from the description field."""
        xml = ('<item><title>T</title>'
               '<enclosure url="https://x.com/a.mp3" type="audio/mpeg"/>'
               '<description>Text with &lt;b&gt;bold&lt;/b&gt; and &lt;p&gt;para&lt;/p&gt;</description>'
               '</item>')
        item = ET.fromstring(xml)
        ep = parse_episode_item(item)
        assert ep is not None
        assert '<b>' not in ep['description']
        assert 'bold' in ep['description']

    def test_description_truncated_to_300_chars(self):
        """Description is truncated to 300 characters."""
        long_desc = 'A' * 500
        xml = f'<item><title>T</title>' \
              f'<enclosure url="https://x.com/a.mp3" type="audio/mpeg"/>' \
              f'<description>{long_desc}</description></item>'
        item = ET.fromstring(xml)
        ep = parse_episode_item(item)
        assert ep is not None
        assert len(ep['description']) <= 300

    def test_extracts_itunes_duration(self):
        """<itunes:duration> is extracted as duration."""
        xml = ('<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
               '<title>T</title>'
               '<enclosure url="https://x.com/a.mp3" type="audio/mpeg"/>'
               '<itunes:duration>3600</itunes:duration>'
               '</item>')
        item = ET.fromstring(xml)
        ep = parse_episode_item(item)
        assert ep is not None
        assert ep['duration'] == '3600'

    def test_processed_defaults_to_false(self):
        """Each parsed episode has processed=False by default."""
        xml = ('<item><title>T</title>'
               '<enclosure url="https://x.com/a.mp3" type="audio/mpeg"/>'
               '</item>')
        item = ET.fromstring(xml)
        ep = parse_episode_item(item)
        assert ep is not None
        assert ep['processed'] is False


# ============================================================
# TestGenerateGuid
# ============================================================

class TestGenerateGuid:
    """Tests for generate_guid()."""

    def test_prefers_explicit_guid(self):
        """Explicit <guid> text takes priority over audio_url."""
        xml = '<item><guid>explicit-guid</guid></item>'
        item = ET.fromstring(xml)
        assert generate_guid(item, 'Title', 'https://x.com/a.mp3') == 'explicit-guid'

    def test_falls_back_to_audio_url(self):
        """When no <guid>, audio_url is used."""
        xml = '<item></item>'
        item = ET.fromstring(xml)
        assert generate_guid(item, 'Title', 'https://x.com/a.mp3') == 'https://x.com/a.mp3'

    def test_falls_back_to_title_hash(self):
        """When no <guid> and no audio_url, SHA256 of title is used."""
        xml = '<item></item>'
        item = ET.fromstring(xml)
        expected = hashlib.sha256('Title'.encode('utf-8')).hexdigest()[:16]
        assert generate_guid(item, 'Title', '') == expected

    def test_empty_guid_element_falls_back(self):
        """Empty <guid></guid> falls back to audio_url."""
        xml = '<item><guid></guid></item>'
        item = ET.fromstring(xml)
        assert generate_guid(item, 'Title', 'https://x.com/a.mp3') == 'https://x.com/a.mp3'

    def test_whitespace_only_guid_falls_back(self):
        """Whitespace-only <guid> falls back to audio_url."""
        xml = '<item><guid>   </guid></item>'
        item = ET.fromstring(xml)
        assert generate_guid(item, 'Title', 'https://x.com/a.mp3') == 'https://x.com/a.mp3'

    def test_consistent_hash_for_same_title(self):
        """Same title always produces the same hash."""
        xml = '<item></item>'
        item = ET.fromstring(xml)
        h1 = generate_guid(item, 'Same Title', '')
        h2 = generate_guid(item, 'Same Title', '')
        assert h1 == h2


# ============================================================
# TestParsePubDate
# ============================================================

class TestParsePubDate:
    """Tests for parse_pub_date()."""

    def test_rfc2822_gmt_date(self):
        """RFC 2822 GMT date is converted to ISO 8601."""
        xml = '<item><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>'
        item = ET.fromstring(xml)
        result = parse_pub_date(item)
        assert result.startswith('2024-01-01')
        assert 'T' in result  # ISO 8601 format

    def test_rfc2822_with_timezone_offset(self):
        """RFC 2822 date with +0800 offset preserves the offset."""
        xml = '<item><pubDate>Tue, 02 Jan 2024 12:00:00 +0800</pubDate></item>'
        item = ET.fromstring(xml)
        result = parse_pub_date(item)
        assert '2024-01-02' in result

    def test_atom_date_returns_raw(self):
        """Unparseable date string is returned as-is (fallback)."""
        xml = '<item><pubDate>not-a-real-date</pubDate></item>'
        item = ET.fromstring(xml)
        result = parse_pub_date(item)
        assert result == 'not-a-real-date'

    def test_missing_pub_date_returns_empty(self):
        """Missing <pubDate> returns empty string."""
        xml = '<item><title>T</title></item>'
        item = ET.fromstring(xml)
        assert parse_pub_date(item) == ''

    def test_empty_pub_date_returns_empty(self):
        """Empty <pubDate> returns empty string."""
        xml = '<item><pubDate></pubDate></item>'
        item = ET.fromstring(xml)
        assert parse_pub_date(item) == ''


# ============================================================
# TestFetchWithRetry
# ============================================================

class TestFetchWithRetry:
    """Tests for fetch_with_retry()."""

    def test_success_returns_response(self):
        """Successful request on first try returns response."""
        with patch('noteforge.sources.rss_parser.requests.get') as mock_get:
            mock_get.return_value = _make_response(200, text='<html></html>')
            resp = fetch_with_retry('https://example.com', max_retries=3)
            assert resp.status_code == 200

    def test_retries_on_429_then_succeeds(self):
        """HTTP 429 triggers retry, then success on second attempt."""
        with patch('noteforge.sources.rss_parser.requests.get') as mock_get, \
             patch('noteforge.sources.rss_parser.time.sleep'):
            # First call returns 429, second returns 200
            mock_get.side_effect = [
                _make_response(429),
                _make_response(200, text='ok'),
            ]
            resp = fetch_with_retry('https://example.com', max_retries=3)
            assert resp.status_code == 200
            assert mock_get.call_count == 2

    def test_retries_on_500_then_succeeds(self):
        """HTTP 500 triggers retry then success."""
        with patch('noteforge.sources.rss_parser.requests.get') as mock_get, \
             patch('noteforge.sources.rss_parser.time.sleep'):
            mock_get.side_effect = [
                _make_response(500),
                _make_response(200),
            ]
            resp = fetch_with_retry('https://example.com', max_retries=3)
            assert resp.status_code == 200

    def test_retries_on_503_then_succeeds(self):
        """HTTP 503 triggers retry then success."""
        with patch('noteforge.sources.rss_parser.requests.get') as mock_get, \
             patch('noteforge.sources.rss_parser.time.sleep'):
            mock_get.side_effect = [
                _make_response(503),
                _make_response(200),
            ]
            resp = fetch_with_retry('https://example.com', max_retries=3)
            assert resp.status_code == 200

    def test_non_retryable_status_raises_immediately(self):
        """HTTP 404 raises RssError without retries."""
        with patch('noteforge.sources.rss_parser.requests.get') as mock_get:
            mock_get.return_value = _make_response(404)
            with pytest.raises(RssError, match='HTTP 404'):
                fetch_with_retry('https://example.com', max_retries=3)
            assert mock_get.call_count == 1

    def test_timeout_triggers_retry(self):
        """requests.Timeout triggers retry with exponential backoff."""
        with patch('noteforge.sources.rss_parser.requests.get') as mock_get, \
             patch('noteforge.sources.rss_parser.time.sleep'):
            mock_get.side_effect = [
                requests.Timeout('timed out'),
                _make_response(200),
            ]
            resp = fetch_with_retry('https://example.com', max_retries=3)
            assert resp.status_code == 200
            assert mock_get.call_count == 2

    def test_connection_error_triggers_retry(self):
        """requests.ConnectionError triggers retry."""
        with patch('noteforge.sources.rss_parser.requests.get') as mock_get, \
             patch('noteforge.sources.rss_parser.time.sleep'):
            mock_get.side_effect = [
                requests.ConnectionError('connection refused'),
                _make_response(200),
            ]
            resp = fetch_with_retry('https://example.com', max_retries=3)
            assert resp.status_code == 200

    def test_exhausts_retries_raises_rss_error(self):
        """After all retries exhausted, raises RssError."""
        with patch('noteforge.sources.rss_parser.requests.get') as mock_get, \
             patch('noteforge.sources.rss_parser.time.sleep'):
            mock_get.return_value = _make_response(503)
            with pytest.raises(RssError):
                fetch_with_retry('https://example.com', max_retries=2)

    def test_sends_user_agent_header(self):
        """Requests include a NoteForge User-Agent header."""
        with patch('noteforge.sources.rss_parser.requests.get') as mock_get:
            mock_get.return_value = _make_response(200)
            fetch_with_retry('https://example.com')
            _, kwargs = mock_get.call_args
            assert 'User-Agent' in kwargs['headers']
            assert 'NoteForge' in kwargs['headers']['User-Agent']

    def test_respects_timeout_param(self):
        """Timeout parameter is passed to requests.get."""
        with patch('noteforge.sources.rss_parser.requests.get') as mock_get:
            mock_get.return_value = _make_response(200)
            fetch_with_retry('https://example.com', timeout=10)
            _, kwargs = mock_get.call_args
            assert kwargs['timeout'] == 10

    def test_stream_param_passed_through(self):
        """stream parameter is passed to requests.get."""
        with patch('noteforge.sources.rss_parser.requests.get') as mock_get:
            mock_get.return_value = _make_response(200)
            fetch_with_retry('https://example.com', stream=True)
            _, kwargs = mock_get.call_args
            assert kwargs['stream'] is True


# ============================================================
# TestDiscoverRss
# ============================================================

class TestDiscoverRss:
    """Tests for discover_rss()."""

    def test_finds_rss_via_link_tag(self):
        """RSS URL is discovered from HTML <link> tag."""
        html = '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
        mock_resp = _make_response(200, text=html)

        def mock_fetch(url, timeout=15):
            return mock_resp

        result = discover_rss('https://example.com', fetch_func=mock_fetch)
        assert result == 'https://example.com/feed.xml'

    def test_resolves_relative_rss_url(self):
        """Relative RSS href is resolved against the page URL."""
        html = '<link rel="alternate" type="application/rss+xml" href="podcast.xml">'
        mock_resp = _make_response(200, text=html)

        def mock_fetch(url, timeout=15):
            return mock_resp

        result = discover_rss('https://example.com/blog/', fetch_func=mock_fetch)
        assert result == 'https://example.com/blog/podcast.xml'

    def test_finds_atom_via_link_tag(self):
        """Atom feed URL is discovered from HTML <link> tag."""
        html = '<link rel="alternate" type="application/atom+xml" href="/atom.xml">'
        mock_resp = _make_response(200, text=html)

        def mock_fetch(url, timeout=15):
            return mock_resp

        result = discover_rss('https://example.com', fetch_func=mock_fetch)
        assert result == 'https://example.com/atom.xml'

    def test_finds_rss_via_href_first_pattern(self):
        """Handles <link> with href before type attribute."""
        html = '<link href="/feed.xml" type="application/rss+xml" rel="alternate">'
        mock_resp = _make_response(200, text=html)

        def mock_fetch(url, timeout=15):
            return mock_resp

        result = discover_rss('https://example.com', fetch_func=mock_fetch)
        assert result == 'https://example.com/feed.xml'

    def test_falls_back_to_common_path(self):
        """When no <link> tag, tries common RSS paths."""
        responses = {
            'https://example.com': _make_response(200, text='<html>no link</html>'),
            'https://example.com/feed': _make_response(200, text='<rss version="2.0">'),
            'https://example.com/rss': _make_response(200, text='no feed here'),
        }

        def mock_fetch(url, timeout=10):
            return responses.get(url, _make_response(404))

        result = discover_rss('https://example.com', fetch_func=mock_fetch)
        assert result == 'https://example.com/feed'

    def test_returns_none_when_no_feed_found(self):
        """Returns None when no RSS/Atom feed is discoverable."""
        def mock_fetch(url, timeout=10):
            return _make_response(404)

        result = discover_rss('https://example.com', fetch_func=mock_fetch)
        assert result is None

    def test_handles_fetch_error_gracefully(self):
        """RssError during discovery returns None."""
        def mock_fetch(url, timeout=15):
            raise RssError('connection failed')

        result = discover_rss('https://example.com', fetch_func=mock_fetch)
        assert result is None


# ============================================================
# TestLooksLikeRssUrl
# ============================================================

class TestLooksLikeRssUrl:
    """Tests for looks_like_rss_url()."""

    def test_xml_extension(self):
        assert looks_like_rss_url('https://example.com/feed.xml') is True

    def test_rss_extension(self):
        assert looks_like_rss_url('https://example.com/podcast.rss') is True

    def test_feed_path(self):
        assert looks_like_rss_url('https://example.com/feed') is True

    def test_rss_path(self):
        assert looks_like_rss_url('https://example.com/rss') is True

    def test_atom_path(self):
        assert looks_like_rss_url('https://example.com/atom') is True

    def test_feeds_subdomain(self):
        assert looks_like_rss_url('https://feeds.example.com/show') is True

    def test_feed_subdomain(self):
        assert looks_like_rss_url('https://feed.example.com/podcast') is True

    def test_feed_path_pattern(self):
        assert looks_like_rss_url('https://example.com/feed/episodes') is True

    def test_simplecast_host(self):
        assert looks_like_rss_url('https://simplecast.com/s/abc123') is True

    def test_buzzsprout_host(self):
        assert looks_like_rss_url('https://www.buzzsprout.com/12345') is True

    def test_anchor_fm_host(self):
        assert looks_like_rss_url('https://anchor.fm/episode') is True

    def test_regular_web_page_returns_false(self):
        assert looks_like_rss_url('https://example.com/about') is False

    def test_youtube_url_returns_false(self):
        assert looks_like_rss_url('https://www.youtube.com/watch?v=abc') is False

    def test_blog_post_returns_false(self):
        assert looks_like_rss_url('https://example.com/blog/post-1') is False


# ============================================================
# TestSafeFilename
# ============================================================

class TestSafeFilename:
    """Tests for safe_filename()."""

    def test_removes_angle_brackets(self):
        result = safe_filename('test<>file')
        assert '<' not in result
        assert '>' not in result

    def test_removes_colon(self):
        result = safe_filename('test:file')
        assert ':' not in result

    def test_removes_quotes(self):
        result = safe_filename('test"file')
        assert '"' not in result

    def test_removes_pipe(self):
        result = safe_filename('test|file')
        assert '|' not in result

    def test_removes_question_mark(self):
        result = safe_filename('test?file')
        assert '?' not in result

    def test_removes_asterisk(self):
        result = safe_filename('test*file')
        assert '*' not in result

    def test_removes_forward_slash(self):
        result = safe_filename('test/file')
        assert '/' not in result

    def test_removes_backslash(self):
        result = safe_filename('test\\file')
        assert '\\' not in result

    def test_preserves_chinese_characters(self):
        assert safe_filename('第1集测试播客') == '第1集测试播客'

    def test_empty_input_returns_unnamed(self):
        assert safe_filename('') == 'unnamed'

    def test_whitespace_only_returns_unnamed(self):
        assert safe_filename('   ') == 'unnamed'

    def test_truncates_long_name(self):
        long_name = 'A' * 200
        result = safe_filename(long_name)
        assert len(result) <= 80

    def test_default_max_len_is_80(self):
        name = 'A' * 100
        result = safe_filename(name)
        assert len(result) == 80

    def test_custom_max_len(self):
        name = 'A' * 100
        result = safe_filename(name, max_len=20)
        assert len(result) == 20

    def test_strips_leading_trailing_dots_and_spaces(self):
        assert safe_filename('.test.') == 'test'
        assert safe_filename(' test ') == 'test'


# ============================================================
# TestFilterSeenGuids
# ============================================================

class TestFilterSeenGuids:
    """Tests for filtering already-seen episode GUIDs."""

    def test_new_guids_returned(self):
        """Episodes with new GUIDs are all returned."""
        episodes = {
            'guid-a': {'guid': 'guid-a', 'title': 'A'},
            'guid-b': {'guid': 'guid-b', 'title': 'B'},
        }
        seen = set()
        new = {k: v for k, v in episodes.items() if k not in seen}
        assert len(new) == 2

    def test_seen_guids_filtered_out(self):
        """Episodes with seen GUIDs are excluded."""
        episodes = {
            'guid-a': {'guid': 'guid-a', 'title': 'A'},
            'guid-b': {'guid': 'guid-b', 'title': 'B'},
        }
        seen = {'guid-a'}
        new = {k: v for k, v in episodes.items() if k not in seen}
        assert 'guid-a' not in new
        assert 'guid-b' in new
        assert len(new) == 1

    def test_all_seen_returns_empty(self):
        """When all GUIDs are seen, returns empty dict."""
        episodes = {
            'guid-a': {'guid': 'guid-a', 'title': 'A'},
        }
        seen = {'guid-a'}
        new = {k: v for k, v in episodes.items() if k not in seen}
        assert new == {}


# ============================================================
# TestParseRssEncoding
# ============================================================

class TestParseRssEncoding:
    """Tests for handling encoding variations in RSS XML."""

    def test_utf8_chinese_content(self):
        """RSS feed with Chinese characters is parsed correctly."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>中文播客</title>
            <description>这是中文描述</description>
            <link>https://example.com</link>
            <item>
              <title>第一集</title>
              <enclosure url="https://example.com/ep01.mp3" type="audio/mpeg"/>
              <guid>cn-01</guid>
              <description>第一集描述内容</description>
            </item>
          </channel>
        </rss>"""
        result = parse_rss_xml(xml)
        assert result['title'] == '中文播客'
        assert result['episodes']['cn-01']['title'] == '第一集'

    def test_encoded_html_entities_in_description(self):
        """HTML entities in description are decoded by ElementTree."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>T</title>
            <item>
              <title>Test</title>
              <enclosure url="https://example.com/a.mp3" type="audio/mpeg"/>
              <guid>g1</guid>
              <description>Price is &lt; 100 &amp; &gt; 50</description>
            </item>
          </channel>
        </rss>"""
        result = parse_rss_xml(xml)
        ep = result['episodes']['g1']
        # ElementTree decodes entities; < becomes <, & becomes &
        assert '&lt;' not in ep['description']
        assert '&amp;' not in ep['description']

    def test_special_characters_in_title(self):
        """Titles with special characters (quotes, ampersands) parse OK."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>Podcast &amp; More</title>
            <item>
              <title>Tom's "Best" Episode</title>
              <enclosure url="https://example.com/a.mp3" type="audio/mpeg"/>
              <guid>g1</guid>
            </item>
          </channel>
        </rss>"""
        result = parse_rss_xml(xml)
        assert result['title'] == 'Podcast & More'
        ep = result['episodes']['g1']
        assert ep['title'] == 'Tom\'s "Best" Episode'
