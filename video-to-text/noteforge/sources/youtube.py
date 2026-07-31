# -*- coding: utf-8 -*-
"""
NoteForge YouTube 数据源

封装 YouTube 下载逻辑为 Source 实现，同时保留 YouTubeHandler 向后兼容。

两个导出：
  - YouTubeSource   — Source 实现（SourceRegistry 路由用）
  - YouTubeHandler  — 旧版类（CLI 直调用用，保持兼容）
"""

import os
import re
import subprocess
import json
import logging
from typing import List, Dict, Optional

from noteforge.sources.downloader import _run_ytdlp_download
from noteforge.sources.base import Source

logger = logging.getLogger('noteforge.sources.youtube')

# YouTube URL 匹配模式
_YOUTUBE_PATTERNS = [
    r'youtube\.com/watch\?v=',
    r'youtu\.be/',
    r'youtube\.com/embed/',
    r'youtube\.com/v/',
]


def _find_ytdlp() -> Optional[str]:
    """查找 yt-dlp 可执行文件"""
    for cmd in ['yt-dlp', 'python -m yt_dlp']:
        try:
            r = subprocess.run(
                cmd.split() + ['--version'],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return None


def _extract_video_id(url: str) -> str:
    for pat in [
        r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:embed/)([a-zA-Z0-9_-]{11})',
    ]:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return 'unknown'


def _build_ytdlp_cmd(ytdlp_path: str, args: List[str]) -> List[str]:
    return ytdlp_path.split() + args


def _extract_metadata(url: str, ytdlp_path: str) -> Dict:
    """提取视频元数据"""
    cmd = _build_ytdlp_cmd(ytdlp_path, [
        '--dump-json', '--no-download', '--no-playlist', url
    ])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return {'id': _extract_video_id(url), 'url': url}
        data = json.loads(r.stdout)
        return {
            'id': data.get('id', ''),
            'title': data.get('title', ''),
            'channel': data.get('channel', data.get('uploader', '')),
            'duration': data.get('duration', 0),
            'upload_date': data.get('upload_date', ''),
            'description': data.get('description', '')[:500],
            'url': url,
            'tags': data.get('tags', [])[:10],
        }
    except (json.JSONDecodeError, subprocess.TimeoutExpired):
        return {'id': _extract_video_id(url), 'url': url}


# ================================================================
# YouTubeSource — Source 实现（SourceRegistry 路由用）
# ================================================================

class YouTubeSource(Source):
    """YouTube 视频数据源（无状态，每次 fetch 时初始化）"""

    def can_handle(self, input_str: str) -> bool:
        if not input_str:
            return False
        return any(re.search(p, input_str) for p in _YOUTUBE_PATTERNS)

    def fetch(self, input_str: str, output_dir: str = "") -> 'FetchResult':
        from noteforge.sources.base import FetchResult

        ytdlp_path = _find_ytdlp()
        if not ytdlp_path:
            return FetchResult(error="yt-dlp 未安装，请运行: pip install yt-dlp")

        if 'playlist' in input_str or 'list=' in input_str:
            return FetchResult(
                error="播放列表请使用 --youtube-playlist 参数",
                metadata={'is_playlist': True, 'url': input_str},
            )

        try:
            metadata = _extract_metadata(input_str, ytdlp_path)
            video_id = metadata.get('id') or _extract_video_id(input_str)
            title = metadata.get('title', video_id)

            audio_path = self._download_audio(input_str, video_id, title, output_dir, ytdlp_path)
            if not audio_path:
                return FetchResult(error="下载音频失败", title=title)

            return FetchResult(
                audio_path=audio_path,
                title=title,
                source_type='youtube',
                metadata={
                    'id': metadata.get('id', video_id),
                    'channel': metadata.get('channel', ''),
                    'duration': metadata.get('duration', 0),
                    'url': input_str,
                },
            )
        except Exception as e:
            logger.error(f"YouTube 处理失败: {e}")
            return FetchResult(error=f"YouTube 处理失败: {e}")

    def _download_audio(self, url: str, video_id: str, title: str,
                        output_dir: str, ytdlp_path: str) -> Optional[str]:
        if not output_dir:
            output_dir = str(
                Path(__file__).resolve().parent.parent.parent / 'output' / 'audio'
            )
        os.makedirs(output_dir, exist_ok=True)

        safe_title = re.sub(r'[<>:"/\\|?*]', '_', title or video_id)[:80]
        output_path = os.path.join(output_dir, f"{safe_title}.mp3")

        if os.path.exists(output_path):
            return output_path

        downloaded = _run_ytdlp_download(url, output_dir)
        if not downloaded:
            return None

        # 重命名为标准文件名
        if downloaded != output_path:
            os.replace(downloaded, output_path)
        return output_path

    @property
    def name(self) -> str:
        return "YouTubeSource"


# ================================================================
# YouTubeHandler — 旧版类（向后兼容，CLI 直调用）
# ================================================================

class YouTubeError(Exception):
    """YouTube 处理异常"""
    pass


class YouTubeHandler:
    """YouTube 音频下载和元数据提取（旧版，保持 CLI 兼容）"""

    def __init__(self, output_dir: str = None, temp_dir: str = None):
        self.output_dir = output_dir or 'output/audio'
        self.temp_dir = temp_dir or 'temp'
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        self._ytdlp_path = _find_ytdlp()
        if not self._ytdlp_path:
            raise YouTubeError("yt-dlp 未安装。请运行: pip install yt-dlp")

    def download_audio(self, url: str, filename: str = None) -> Dict:
        logger.info(f"下载音频: {url}")
        metadata = _extract_metadata(url, self._ytdlp_path)
        video_id = metadata.get('id') or _extract_video_id(url)
        title = metadata.get('title', video_id)

        if filename is None:
            safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)[:80]
            filename = safe_title

        output_path = os.path.join(self.output_dir, f"{filename}.mp3")
        if os.path.exists(output_path):
            logger.info(f"音频已存在，跳过下载: {output_path}")
            metadata['path'] = output_path
            return metadata

        downloaded = _run_ytdlp_download(url, self.output_dir)
        if downloaded:
            os.replace(downloaded, output_path)
            logger.info(f"音频已保存: {output_path}")
        else:
            raise YouTubeError("下载完成但未找到音频文件")

        metadata['path'] = output_path
        return metadata

    def download_playlist(self, url: str) -> List[Dict]:
        logger.info(f"下载播放列表: {url}")
        cmd = _build_ytdlp_cmd(self._ytdlp_path, [
            '--dump-json', '--no-download', '--flat-playlist', url
        ])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise YouTubeError(f"播放列表元数据提取失败: {r.stderr[:300]}")

        entries = []
        for line in r.stdout.strip().split('\n'):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        results = []
        for i, entry in enumerate(entries, 1):
            video_url = entry.get('url', '')
            if not video_url:
                continue
            logger.info(f"[{i}/{len(entries)}] {entry.get('title', video_url)}")
            try:
                result = self.download_audio(video_url)
                results.append(result)
            except YouTubeError as e:
                logger.error(f"下载失败: {e}")
                results.append({'url': video_url, 'error': str(e)})
        return results
