# -*- coding: utf-8 -*-
"""
NoteForge RSS 解析工具模块

从 podcast.py 提取的 RSS 解析和实用函数，供 PodcastHandler 及其他模块复用。

功能:
- RSS 2.0 + Atom feed 解析
- Episode 条目解析（enclosure / Atom link）
- GUID 生成、日期解析、文件名清理、URL 判断
- RSS 自动发现（HTML <link> 标签 + 常见路径探测）
- 带指数退避的 HTTP GET

依赖:
- xml.etree.ElementTree (stdlib)
- email.utils (stdlib, RFC 2822 日期解析)
- requests (HTTP 请求)
"""

import re
import hashlib
import logging
import time
from typing import Optional, Dict, Callable
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urljoin
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger('noteforge.rss_parser')

# iTunes namespace
ITUNES_NS = 'http://www.itunes.com/dtds/podcast-1.0.dtd'


class RssError(Exception):
    """RSS 解析/请求异常"""
    pass


# ----------------------------------------------------------
# HTTP 请求
# ----------------------------------------------------------

def fetch_with_retry(url: str, max_retries: int = 3,
                     timeout: int = 30,
                     stream: bool = False) -> requests.Response:
    """
    带指数退避的 HTTP GET

    Args:
        url: 请求 URL
        max_retries: 最大重试次数
        timeout: 请求超时（秒）
        stream: 是否流式下载

    Returns:
        requests.Response（status_code == 200）

    Raises:
        RssError: 所有重试失败后
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(
                url, timeout=timeout, stream=stream,
                headers={'User-Agent': 'NoteForge/1.0'}
            )
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503):
                wait = 2 ** attempt * 10
                logger.warning(
                    f"HTTP {resp.status_code}，{wait}s 后重试"
                )
                time.sleep(wait)
                last_error = RssError(f"HTTP {resp.status_code}")
                continue
            raise RssError(f"HTTP {resp.status_code}: {url}")
        except requests.Timeout:
            wait = 2 ** attempt * 15
            logger.warning(f"请求超时，{wait}s 后重试: {url}")
            time.sleep(wait)
            last_error = RssError(f"请求超时: {url}")
        except requests.ConnectionError as e:
            wait = 2 ** attempt * 10
            logger.warning(f"连接失败，{wait}s 后重试: {e}")
            time.sleep(wait)
            last_error = RssError(f"连接失败: {url}")
    raise last_error or RssError(f"请求失败: {url}")


# ----------------------------------------------------------
# RSS 解析
# ----------------------------------------------------------

def parse_rss_xml(xml_text: str) -> dict:
    """
    解析 RSS 2.0 + iTunes namespace

    Args:
        xml_text: RSS/Atom XML 字符串

    Returns:
        {title, description, link, episodes: {guid: episode_dict}}

    Raises:
        ValueError: 无效的 RSS XML
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"无效的 RSS XML: {e}")

    # 支持 RSS 2.0 (<rss><channel>) 和 Atom (<feed>)
    channel = root.find('channel')
    if channel is None:
        # Atom 格式
        channel = root
        if channel.tag != 'feed' and not channel.tag.endswith('}feed'):
            raise ValueError("无效的 RSS feed: 缺少 <channel> 元素")

    title = channel.findtext('title', '').strip()
    description = channel.findtext('description', '').strip()
    link = channel.findtext('link', '').strip()

    episodes: Dict[str, dict] = {}
    items = channel.findall('item')
    if not items:
        # Atom 格式用 <entry>
        items = channel.findall('{http://www.w3.org/2005/Atom}entry')

    for item in items:
        ep = parse_episode_item(item)
        if ep:
            episodes[ep['guid']] = ep

    return {
        'title': title or 'Unknown Podcast',
        'description': description[:500],
        'link': link,
        'episodes': episodes,
    }


def parse_episode_item(item) -> Optional[dict]:
    """
    解析单个 RSS item/entry

    Args:
        item: ElementTree 元素（<item> 或 <entry>）

    Returns:
        episode 字典，无音频链接时返回 None
    """
    # 提取 enclosure（音频链接）
    enclosure = item.find('enclosure')
    audio_url = ''
    audio_type = ''

    if enclosure is not None:
        audio_url = enclosure.get('url', '').strip()
        audio_type = enclosure.get('type', '')

    # Atom 格式: <link rel="enclosure" href="..."/>
    if not audio_url:
        for link in item.findall('{http://www.w3.org/2005/Atom}link'):
            if link.get('rel') == 'enclosure':
                audio_url = link.get('href', '').strip()
                audio_type = link.get('type', '')
                break

    if not audio_url:
        return None

    # 跳过非音频
    if audio_type and not audio_type.startswith('audio/'):
        return None

    # 标题
    title = item.findtext('title', '').strip()
    if not title:
        title = item.findtext(
            '{http://www.w3.org/2005/Atom}title', ''
        ).strip()

    # GUID
    guid = generate_guid(item, title, audio_url)

    # 发布日期
    pub_date = parse_pub_date(item)

    # 时长
    duration = item.findtext(f'{{{ITUNES_NS}}}duration', '')
    if not duration:
        duration = item.findtext('duration', '')

    # 描述
    desc = item.findtext('description', '').strip()
    if not desc:
        desc = item.findtext(
            f'{{{ITUNES_NS}}}summary', ''
        ).strip()
    # 去除 HTML 标签
    desc = re.sub(r'<[^>]+>', '', desc)[:300]

    # 链接
    link = item.findtext('link', '').strip()

    return {
        'guid': guid,
        'title': title,
        'audio_url': audio_url,
        'audio_type': audio_type,
        'duration': duration.strip() if duration else '',
        'pub_date': pub_date,
        'description': desc,
        'link': link,
        'processed': False,
    }


def generate_guid(item, title: str, audio_url: str) -> str:
    """
    生成 episode 唯一标识

    优先级: <guid> → audio_url → title SHA256 前 16 位
    """
    # 优先用 <guid>
    guid_elem = item.findtext('guid', '').strip()
    if guid_elem:
        return guid_elem

    # 其次用 audio_url
    if audio_url:
        return audio_url

    # 最后用 title hash
    return hashlib.sha256(title.encode('utf-8')).hexdigest()[:16]


def parse_pub_date(item) -> str:
    """
    解析 RSS <pubDate>（RFC 2822 格式）

    Returns:
        ISO 8601 字符串，解析失败时返回原始字符串
    """
    raw_date = item.findtext('pubDate', '').strip()
    if not raw_date:
        return ''

    try:
        dt = parsedate_to_datetime(raw_date)
        return dt.isoformat()
    except (ValueError, TypeError):
        return raw_date


# ----------------------------------------------------------
# RSS 自动发现
# ----------------------------------------------------------

def discover_rss(url: str,
                 fetch_func: Callable = None) -> Optional[str]:
    """
    从主页 URL 发现 RSS feed URL

    Args:
        url: 主页 URL
        fetch_func: HTTP GET 函数，签名为 (url, timeout) -> Response
                    默认使用 fetch_with_retry

    Returns:
        RSS feed URL，未发现时返回 None
    """
    if fetch_func is None:
        fetch_func = fetch_with_retry

    # 策略 1: HTML <link> 标签
    try:
        resp = fetch_func(url, timeout=15)
        html = resp.text[:50000]  # 只检查前 50KB

        # <link rel="alternate" type="application/rss+xml" href="...">
        patterns = [
            r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)["\']',
            r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/(?:rss|atom)\+xml["\']',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                rss_url = matches[0]
                if not rss_url.startswith('http'):
                    rss_url = urljoin(url, rss_url)
                return rss_url
    except RssError:
        pass

    # 策略 2: 常见路径
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    common_paths = [
        '/feed', '/rss', '/podcast.xml', '/feed.xml',
        '/rss.xml', '/atom.xml', '/episodes.rss',
    ]
    for path in common_paths:
        try:
            test_url = urljoin(base, path)
            resp = fetch_func(test_url, timeout=10)
            if '<rss' in resp.text[:500] or '<feed' in resp.text[:500]:
                return test_url
        except RssError:
            continue

    logger.warning(f"无法从 {url} 发现 RSS feed")
    return None


# ----------------------------------------------------------
# 实用函数
# ----------------------------------------------------------

def looks_like_rss_url(url: str) -> bool:
    """判断 URL 是否像是 RSS feed"""
    lower = url.lower()
    # 文件扩展名
    if any(lower.endswith(ext) for ext in
           ['.xml', '.rss', '/feed', '/rss', '/atom']):
        return True
    # 已知 feed 托管域名
    feed_hosts = ['feeds.', 'feedsproxy.', 'feed.', 'rss.']
    parsed = urlparse(lower)
    if any(parsed.netloc.startswith(h) for h in feed_hosts):
        return True
    # 已知 podcast 平台 feed 路径
    feed_patterns = ['/feed/', '/rss/', 'simplecast', 'megaphone',
                     'anchor.fm', 'buzzsprout', 'transistor']
    if any(p in lower for p in feed_patterns):
        return True
    return False


def safe_filename(name: str, max_len: int = 80) -> str:
    """清理文件名中的非法字符"""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    safe = safe.strip('. ')
    return safe[:max_len] if safe else 'unnamed'
