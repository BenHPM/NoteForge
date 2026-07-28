# -*- coding: utf-8 -*-
"""
NoteForge Podcast RSS 处理模块 v1.1
功能:
- 订阅/取消订阅 podcast RSS 源
- 解析 RSS 2.0 + iTunes namespace，提取 episode 列表
- 下载 episode 音频（流式，带进度）
- 跟踪已处理 episode，避免重复下载
- 从 podcast 主页自动发现 RSS URL

依赖:
- requests (已安装)
- rss_parser (RSS 解析 + HTTP + 实用函数)
"""

import os
import re
import json
import shutil
import logging
from typing import List, Optional
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict

from noteforge.sources.base import FetchResult
from noteforge.sources.rss_parser import (
    parse_rss_xml,
    looks_like_rss_url,
    safe_filename,
    discover_rss,
    fetch_with_retry,
    RssError,
)

logger = logging.getLogger('noteforge.podcast')


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
        is_rss = looks_like_rss_url(feed_url)
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
            slug = safe_filename(name).lower().replace(' ', '-')
        else:
            slug = safe_filename(feed_data['title']).lower().replace(' ', '-')

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
        def _sort_key(e):
            return e.pub_date if e.pub_date else ''
        episodes.sort(key=_sort_key, reverse=True)
        return episodes

    # ----------------------------------------------------------
    # RSS 解析
    # ----------------------------------------------------------

    def fetch_feed(self, feed_url: str) -> dict:
        """获取并解析 RSS feed"""
        try:
            resp = fetch_with_retry(feed_url, timeout=30)
        except RssError as e:
            raise PodcastError(str(e))
        try:
            return parse_rss_xml(resp.text)
        except ValueError as e:
            raise PodcastError(str(e))

    # ----------------------------------------------------------
    # RSS 自动发现
    # ----------------------------------------------------------

    def discover_rss(self, url: str) -> Optional[str]:
        """从主页 URL 发现 RSS feed URL"""
        try:
            return discover_rss(url)
        except RssError as e:
            raise PodcastError(str(e))

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

        safe_name = safe_filename(episode.title)
        podcast_dir = os.path.join(self.output_dir, feed_name or 'unknown')
        os.makedirs(podcast_dir, exist_ok=True)
        output_path = os.path.join(podcast_dir, f"{safe_name}.mp3")

        # 跳过已下载
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
            logger.info(f"音频已存在，跳过: {output_path}")
            return output_path

        # 流式下载
        logger.info(f"下载: {episode.title}")
        try:
            resp = fetch_with_retry(
                episode.audio_url, stream=True, timeout=300
            )
        except RssError as e:
            raise PodcastError(str(e))

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


# ================================================================
# PodcastSource — Source 实现（SourceRegistry 路由用）
# ================================================================

class PodcastSource:
    """Podcast RSS feed 数据源（轻量实现）

    与 PodcastHandler 互补：PodcastHandler 负责订阅管理，
    PodcastSource 负责获取 episode 音频（SourceRegistry 路由用）。
    """

    def can_handle(self, input_str: str) -> bool:
        if not input_str:
            return False
        if not input_str.startswith(('http://', 'https://', 'feed://')):
            return False
        if looks_like_rss_url(input_str):
            return True
        if any(k in input_str.lower() for k in [
            'feeds.soundcloud.com', 'feeds.feedburner.com',
            'podcasts.apple.com', 'open.spotify.com/show',
        ]):
            return True
        # 直接音频 URL（兜底）
        if re.search(r'\.(mp3|m4a|wav|ogg|opus)(\?|$)', input_str):
            return True
        return False

    def fetch(self, input_str: str, output_dir: str = "") -> FetchResult:
        from noteforge.sources.downloader import _run_ytdlp_download

        if not output_dir:
            output_dir = str(
                Path(__file__).resolve().parent.parent.parent / 'output' / 'audio'
            )
        os.makedirs(output_dir, exist_ok=True)

        # 单集音频 URL 直接下载
        if re.search(r'\.(mp3|m4a|wav|ogg|opus)(\?|$)', input_str):
            path = _run_ytdlp_download(input_str, output_dir)
            if path:
                return FetchResult(
                    audio_path=path,
                    title=os.path.splitext(os.path.basename(path))[0],
                    source_type='podcast',
                    metadata={'url': input_str},
                )
            return FetchResult(error=f"Podcast 音频下载失败: {input_str}")

        # feed URL — 尝试 yt-dlp 下载
        try:
            feed_url = discover_rss(input_str) or input_str
            path = _run_ytdlp_download(feed_url, output_dir)
            if path:
                return FetchResult(
                    audio_path=path,
                    title=os.path.splitext(os.path.basename(path))[0],
                    source_type='podcast',
                    metadata={'feed_url': feed_url},
                )
        except Exception as e:
            logger.warning(f"Podcast feed 下载失败: {e}")

        return FetchResult(
            error=(
                f"Podcast 下载失败，请使用订阅命令: "
                f"python -m noteforge --podcast-subscribe {input_str}"
            ),
        )

    @property
    def name(self) -> str:
        return "PodcastSource"
