"""
NoteForge Podcast RSS 处理模块 v1.0
功能:
- 订阅/取消订阅 podcast RSS 源
- 解析 RSS 2.0 + iTunes namespace，提取 episode 列表
- 下载 episode 音频（流式，带进度）
- 跟踪已处理 episode，避免重复下载
- 从 podcast 主页自动发现 RSS URL

依赖:
- requests (已安装)
- xml.etree.ElementTree (stdlib)
- email.utils (stdlib, RFC 2822 日期解析)
"""

import os
import re
import json
import hashlib
import shutil
import logging
import time
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime
from dataclasses import dataclass, asdict, field
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger('noteforge.podcast')

# iTunes namespace
ITUNES_NS = 'http://www.itunes.com/dtds/podcast-1.0.dtd'


class PodcastError(Exception):
    """Podcast 处理异常"""
    pass


@dataclass
class Episode:
    """单个 episode 信息"""
    guid: str                   # RSS <guid> 或 link/title hash
    title: str
    audio_url: str              # <enclosure url>
    audio_type: str = ""        # e.g. "audio/mpeg"
    duration: str = ""          # <itunes:duration>
    pub_date: str = ""          # ISO 8601
    description: str = ""       # <description> 或 <itunes:summary>
    link: str = ""              # episode 网页
    processed: bool = False
    local_audio_path: str = ""
    transcript_path: str = ""
    note_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class PodcastHandler:
    """Podcast RSS 订阅和 episode 下载管理"""

    def __init__(self, config_path: str = None, output_dir: str = None,
                 temp_dir: str = None):
        self.config_path = config_path or 'config/podcast_feeds.json'
        self.output_dir = output_dir or 'output/audio/podcasts'
        self.temp_dir = temp_dir or 'temp'
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)

    # ----------------------------------------------------------
    # Feed 管理
    # ----------------------------------------------------------

    def subscribe(self, feed_url: str, name: str = None,
                  auto_discover: bool = True) -> dict:
        """
        订阅一个 podcast feed

        Args:
            feed_url: RSS URL 或主页 URL
            name: 手动指定 feed 名称
            auto_discover: 是否尝试自动发现 RSS

        Returns:
            feed 元数据字典
        """
        # 尝试直接作为 RSS 获取
        is_rss = self._looks_like_rss_url(feed_url)
        if not is_rss:
            # 先尝试直接获取看是否是 RSS
            try:
                feed_data = self.fetch_feed(feed_url)
                if feed_data.get('episodes'):
                    logger.info(f"URL 直接返回有效 RSS feed")
                    is_rss = True
            except Exception as e:
                logger.debug(f"操作失败: {e}")
                pass

        if not is_rss and auto_discover:
            logger.info(f"尝试从 {feed_url} 发现 RSS feed...")
            discovered = self.discover_rss(feed_url)
            if discovered:
                logger.info(f"发现 RSS: {discovered}")
                feed_url = discovered
            else:
                raise PodcastError(
                    f"无法从 {feed_url} 发现 RSS feed。请直接提供 RSS URL。"
                )

        # 获取并解析 feed
        feed_data = self.fetch_feed(feed_url)

        # 生成 slug 名称
        if name:
            slug = self._safe_filename(name).lower().replace(' ', '-')
        else:
            slug = self._safe_filename(feed_data['title']).lower().replace(' ', '-')

        # 加载配置
        config = self._load_feeds_config()

        # 检查是否已订阅
        if slug in config['feeds']:
            logger.info(f"已订阅: {slug}，更新 episodes...")
            existing = config['feeds'][slug]
            existing['last_synced_at'] = datetime.now().isoformat()
            # 合并新 episodes
            for guid, ep_data in feed_data['episodes'].items():
                if guid not in existing['episodes']:
                    existing['episodes'][guid] = ep_data
            existing['last_episode_count'] = len(existing['episodes'])
            self._save_feeds_config(config)
            return {
                'name': existing['name'],
                'slug': slug,
                'feed_url': feed_url,
                'episode_count': len(existing['episodes']),
                'new_episodes': len(feed_data['episodes']),
            }

        # 新订阅
        now = datetime.now().isoformat()
        config['feeds'][slug] = {
            'name': feed_data['title'],
            'feed_url': feed_url,
            'homepage_url': feed_data.get('link', ''),
            'subscribed_at': now,
            'last_synced_at': now,
            'last_episode_count': len(feed_data['episodes']),
            'episodes': feed_data['episodes'],
        }
        self._save_feeds_config(config)

        logger.info(f"已订阅: {feed_data['title']} ({len(feed_data['episodes'])} episodes)")
        return {
            'name': feed_data['title'],
            'slug': slug,
            'feed_url': feed_url,
            'episode_count': len(feed_data['episodes']),
        }

    def unsubscribe(self, feed_name: str) -> bool:
        """取消订阅（不删除已下载的音频）"""
        config = self._load_feeds_config()
        if feed_name not in config['feeds']:
            raise PodcastError(f"未找到订阅: {feed_name}")
        del config['feeds'][feed_name]
        self._save_feeds_config(config)
        logger.info(f"已取消订阅: {feed_name}")
        return True

    def list_feeds(self) -> List[dict]:
        """列出所有已订阅的 feeds"""
        config = self._load_feeds_config()
        result = []
        for slug, feed in config['feeds'].items():
            episodes = feed.get('episodes', {})
            processed = sum(1 for e in episodes.values() if e.get('processed'))
            result.append({
                'slug': slug,
                'name': feed.get('name', slug),
                'feed_url': feed.get('feed_url', ''),
                'total_episodes': len(episodes),
                'processed': processed,
                'new': len(episodes) - processed,
                'last_synced': feed.get('last_synced_at', ''),
            })
        return result

    def list_episodes(self, feed_name: str,
                      only_new: bool = True) -> List[Episode]:
        """列出指定 feed 的 episodes"""
        config = self._load_feeds_config()
        if feed_name not in config['feeds']:
            raise PodcastError(f"未找到订阅: {feed_name}")

        feed = config['feeds'][feed_name]
        episodes: List[Episode] = []

        for guid, ep_data in feed.get('episodes', {}).items():
            processed = ep_data.get('processed', False)
            if only_new and processed:
                continue
            episodes.append(Episode(
                guid=guid,
                title=ep_data.get('title', ''),
                audio_url=ep_data.get('audio_url', ''),
                audio_type=ep_data.get('audio_type', ''),
                duration=ep_data.get('duration', ''),
                pub_date=ep_data.get('pub_date', ''),
                description=ep_data.get('description', '')[:200],
                link=ep_data.get('link', ''),
                processed=processed,
                local_audio_path=ep_data.get('local_audio_path', ''),
                transcript_path=ep_data.get('transcript_path', ''),
                note_path=ep_data.get('note_path', ''),
            ))

        # 按发布日期排序（最新在前）
        # pub_date 为 ISO/RFC 字符串，空字符串排到最后
        def _sort_key(e):
            # 空日期视为最早（排到最后）
            return e.pub_date if e.pub_date else ''
        episodes.sort(key=_sort_key, reverse=True)
        return episodes

    # ----------------------------------------------------------
    # RSS 解析
    # ----------------------------------------------------------

    def fetch_feed(self, feed_url: str) -> dict:
        """获取并解析 RSS feed"""
        resp = self._fetch_with_retry(feed_url, timeout=30)
        return self._parse_rss_xml(resp.text)

    def _parse_rss_xml(self, xml_text: str) -> dict:
        """
        解析 RSS 2.0 + iTunes namespace

        Returns:
            {title, description, link, episodes: {guid: episode_dict}}
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            raise PodcastError(f"无效的 RSS XML: {e}")

        # 支持 RSS 2.0 (<rss><channel>) 和 Atom (<feed>)
        channel = root.find('channel')
        if channel is None:
            # Atom 格式
            channel = root
            if channel.tag != 'feed' and not channel.tag.endswith('}feed'):
                raise PodcastError("无效的 RSS feed: 缺少 <channel> 元素")

        title = channel.findtext('title', '').strip()
        description = channel.findtext('description', '').strip()
        link = channel.findtext('link', '').strip()

        episodes: Dict[str, dict] = {}
        items = channel.findall('item')
        if not items:
            # Atom 格式用 <entry>
            items = channel.findall('{http://www.w3.org/2005/Atom}entry')

        for item in items:
            ep = self._parse_episode_item(item)
            if ep:
                episodes[ep['guid']] = ep

        return {
            'title': title or 'Unknown Podcast',
            'description': description[:500],
            'link': link,
            'episodes': episodes,
        }

    def _parse_episode_item(self, item) -> Optional[dict]:
        """解析单个 RSS item/entry"""
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
        guid = self._generate_guid(item, title, audio_url)

        # 发布日期
        pub_date = self._parse_pub_date(item)

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

    def _generate_guid(self, item, title: str, audio_url: str) -> str:
        """生成 episode 唯一标识"""
        # 优先用 <guid>
        guid_elem = item.findtext('guid', '').strip()
        if guid_elem:
            return guid_elem

        # 其次用 audio_url
        if audio_url:
            return audio_url

        # 最后用 title hash
        return hashlib.sha256(title.encode('utf-8')).hexdigest()[:16]

    def _parse_pub_date(self, item) -> str:
        """解析 RFC 2822 日期"""
        raw_date = item.findtext('pubDate', '').strip()
        if not raw_date:
            return ''

        try:
            dt = parsedate_to_datetime(raw_date)
            return dt.isoformat()
        except (ValueError, TypeError):
            return raw_date

    # ----------------------------------------------------------
    # Episode 下载
    # ----------------------------------------------------------

    def download_episode(self, episode: Episode,
                         feed_name: str = None) -> str:
        """
        下载单个 episode 音频

        Returns:
            本地文件路径
        """
        if not episode.audio_url:
            raise PodcastError(f"Episode 无音频 URL: {episode.title}")

        safe_name = self._safe_filename(episode.title)
        podcast_dir = os.path.join(self.output_dir, feed_name or 'unknown')
        os.makedirs(podcast_dir, exist_ok=True)
        output_path = os.path.join(podcast_dir, f"{safe_name}.mp3")

        # 跳过已下载
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
            logger.info(f"音频已存在，跳过: {output_path}")
            return output_path

        # 流式下载
        logger.info(f"下载: {episode.title}")
        resp = self._fetch_with_retry(
            episode.audio_url, stream=True, timeout=300
        )

        total_size = int(resp.headers.get('content-length', 0))
        downloaded = 0
        tmp_path = output_path + '.tmp'

        with open(tmp_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                # 每 10MB 打印进度
                if total_size > 0 and downloaded % (10 * 1024 * 1024) < 8192:
                    pct = downloaded / total_size * 100
                    logger.info(
                        f"  下载进度: {pct:.0f}% "
                        f"({downloaded // 1024 // 1024}MB/"
                        f"{total_size // 1024 // 1024}MB)"
                    )

        if downloaded < 1024:
            os.unlink(tmp_path)
            raise PodcastError(
                f"下载的文件过小 ({downloaded} bytes): {episode.audio_url}"
            )

        os.rename(tmp_path, output_path)
        logger.info(f"已保存: {output_path} ({downloaded // 1024 // 1024}MB)")
        return output_path

    def download_new_episodes(self, feed_name: str) -> List[Episode]:
        """下载指定 feed 的所有新 episodes"""
        episodes = self.list_episodes(feed_name, only_new=True)
        if not episodes:
            logger.info(f"没有新 episodes: {feed_name}")
            return []

        logger.info(f"下载 {len(episodes)} 个新 episodes: {feed_name}")
        downloaded: List[Episode] = []

        for i, ep in enumerate(episodes, 1):
            logger.info(f"[{i}/{len(episodes)}] {ep.title}")
            try:
                path = self.download_episode(ep, feed_name)
                ep.local_audio_path = path
                downloaded.append(ep)
            except PodcastError as e:
                logger.error(f"下载失败: {e}")

        return downloaded

    # ----------------------------------------------------------
    # RSS 自动发现
    # ----------------------------------------------------------

    def discover_rss(self, url: str) -> Optional[str]:
        """从主页 URL 发现 RSS feed URL"""
        # 策略 1: HTML <link> 标签
        try:
            resp = self._fetch_with_retry(url, timeout=15)
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
        except PodcastError:
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
                resp = self._fetch_with_retry(test_url, timeout=10)
                if '<rss' in resp.text[:500] or '<feed' in resp.text[:500]:
                    return test_url
            except PodcastError:
                continue

        logger.warning(f"无法从 {url} 发现 RSS feed")
        return None

    # ----------------------------------------------------------
    # Episode 处理状态跟踪
    # ----------------------------------------------------------

    def mark_episode_processed(self, feed_name: str, guid: str,
                                transcript_path: str = "",
                                note_path: str = "",
                                local_audio_path: str = ""):
        """标记 episode 为已处理"""
        config = self._load_feeds_config()
        if feed_name not in config['feeds']:
            return

        episodes = config['feeds'][feed_name].get('episodes', {})
        if guid in episodes:
            episodes[guid]['processed'] = True
            episodes[guid]['processed_at'] = datetime.now().isoformat()
            if transcript_path:
                episodes[guid]['transcript_path'] = transcript_path
            if note_path:
                episodes[guid]['note_path'] = note_path
            if local_audio_path:
                episodes[guid]['local_audio_path'] = local_audio_path
            self._save_feeds_config(config)

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _load_feeds_config(self) -> dict:
        """加载 podcast_feeds.json"""
        if not os.path.exists(self.config_path):
            return {"version": "1.0", "feeds": {}}

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            if not isinstance(config.get('feeds'), dict):
                logger.warning("配置文件格式异常，重置")
                return {"version": "1.0", "feeds": {}}
            return config
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"配置文件损坏: {e}。备份并重置。")
            backup_path = self.config_path + '.bak'
            try:
                shutil.copy2(self.config_path, backup_path)
                logger.info(f"已备份到: {backup_path}")
            except Exception:
                pass
            return {"version": "1.0", "feeds": {}}

    def _save_feeds_config(self, config: dict):
        """原子写入 podcast_feeds.json"""
        tmp_path = self.config_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.makedirs(os.path.dirname(self.config_path) or '.', exist_ok=True)
        os.replace(tmp_path, self.config_path)

    def _fetch_with_retry(self, url: str, max_retries: int = 3,
                          timeout: int = 30,
                          stream: bool = False) -> requests.Response:
        """带指数退避的 HTTP GET"""
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
                    last_error = PodcastError(f"HTTP {resp.status_code}")
                    continue
                raise PodcastError(f"HTTP {resp.status_code}: {url}")
            except requests.Timeout:
                wait = 2 ** attempt * 15
                logger.warning(f"请求超时，{wait}s 后重试: {url}")
                time.sleep(wait)
                last_error = PodcastError(f"请求超时: {url}")
            except requests.ConnectionError as e:
                wait = 2 ** attempt * 10
                logger.warning(f"连接失败，{wait}s 后重试: {e}")
                time.sleep(wait)
                last_error = PodcastError(f"连接失败: {url}")
        raise last_error or PodcastError(f"请求失败: {url}")

    @staticmethod
    def _looks_like_rss_url(url: str) -> bool:
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

    @staticmethod
    def _safe_filename(name: str, max_len: int = 80) -> str:
        """清理文件名中的非法字符"""
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
        safe = safe.strip('. ')
        return safe[:max_len] if safe else 'unnamed'
