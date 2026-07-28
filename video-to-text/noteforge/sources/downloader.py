# -*- coding: utf-8 -*-
"""
NoteForge 音频平台下载策略

共享 yt-dlp 下载逻辑（_run_ytdlp_download），同时提供 AudioPlatformSource Source 实现。
平台特有策略（小宇宙/荔枝FM）在此模块。
"""

import os
import re
import json
import subprocess
import glob
import shutil
import logging
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from noteforge.sources.base import Source, FetchResult

logger = logging.getLogger('noteforge.sources.downloader')


# ================================================================
# _run_ytdlp_download — 共享函数（向后兼容）
# ================================================================

def _run_ytdlp_download(url: str, out_dir: str, no_playlist: bool = True) -> str:
    """使用 yt-dlp 下载音频，返回输出文件路径或空字符串。"""
    if not shutil.which('yt-dlp'):
        return ""
    output_tpl = os.path.join(out_dir, '%(title)s.%(ext)s')
    cmd = ["yt-dlp", "--no-update", "--extract-audio", "--audio-format", "mp3"]
    if no_playlist:
        cmd.append("--no-playlist")
    cmd.extend(["-o", output_tpl, url])
    dl = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if dl.returncode != 0:
        return ""
    for line in (dl.stdout + dl.stderr).splitlines():
        if '[ExtractAudio]' in line and 'Destination:' in line:
            p = line.split('Destination:', 1)[1].strip()
            if os.path.exists(p):
                return p
    candidates = glob.glob(os.path.join(out_dir, '*.mp3'))
    return max(candidates, key=os.path.getmtime) if candidates else ""


# ================================================================
# 平台特有下载
# ================================================================

def _download_xiaoyuzhou(eid: str, out_dir: str) -> Optional[Tuple[str, str]]:
    """小宇宙 API 提取，返回 (audio_path, title) 或 None"""
    api = f"https://www.xiaoyuzhoufm.com/api/v1/episode/get?eid={eid}"
    req = urllib.request.Request(api, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    ep = data.get('data', data)
    media = ep.get('media', {})
    audio_url = media.get('src') or ep.get('enclosure', {}).get('url', '')
    if not audio_url:
        return None
    title = ep.get('title', '')
    ext = os.path.splitext(audio_url.split('?')[0])[1] or '.mp3'
    if not ext.startswith('.'):
        ext = '.' + ext
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', title or eid)
    output_path = os.path.join(out_dir, f"{safe_title}{ext}")
    req2 = urllib.request.Request(audio_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.xiaoyuzhoufm.com/",
    })
    with urllib.request.urlopen(req2, timeout=300) as resp:
        with open(output_path, 'wb') as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    return (output_path, title) if os.path.exists(output_path) else None


def _download_lizhi(ep_id: str, out_dir: str) -> Optional[Tuple[str, str]]:
    """荔枝FM API 提取，返回 (audio_path, title) 或 None"""
    api = f"https://www.lizhi.fm/api/audios/episode/{ep_id}"
    req = urllib.request.Request(api, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.lizhi.fm/",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    audio_url = data.get('data', {}).get('audio_url', '')
    if not audio_url:
        return None
    title = data.get('data', {}).get('title', '')
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', title or ep_id)
    output_path = os.path.join(out_dir, f"{safe_title}.mp3")
    req2 = urllib.request.Request(audio_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.lizhi.fm/",
    })
    with urllib.request.urlopen(req2, timeout=300) as resp:
        with open(output_path, 'wb') as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    return (output_path, title) if os.path.exists(output_path) else None


# ================================================================
# MediaDownloader — 旧版静态类（向后兼容）
# ================================================================

class MediaDownloader:
    """音频平台下载策略（yt-dlp + 平台 API 降级，旧版）"""

    @staticmethod
    def try_ytdlp(url, out_dir):
        result = _run_ytdlp_download(url, out_dir)
        return result or None

    @staticmethod
    def try_xiaoyuzhou(url, out_dir):
        m = re.search(r'xiaoyuzhoufm\.com/episode/([a-f0-9]+)', url)
        if not m:
            return None
        return _download_xiaoyuzhou(m.group(1), out_dir)

    @staticmethod
    def try_lizhi(url, out_dir):
        m = re.search(r'lizhi\.fm/(?:episode/)?(\d+)', url)
        if not m:
            return None
        return _download_lizhi(m.group(1), out_dir)


# ================================================================
# AudioPlatformSource — Source 实现
# ================================================================

class AudioPlatformSource:
    """音频平台数据源（小宇宙 / 荔枝FM / 喜马拉雅等）

    使用 yt-dlp 通用提取 + 平台 API 降级链。
    """

    _PLATFORMS = {
        'xiaoyuzhou': r'xiaoyuzhoufm\.com/episode/([a-f0-9]+)',
        'lizhi': r'lizhi\.fm/(?:episode/)?(\d+)',
        'ximalaya': r'ximalaya\.com/(?:track|album)/',
    }

    def can_handle(self, input_str: str) -> bool:
        if not input_str:
            return False
        for pat in self._PLATFORMS.values():
            if re.search(pat, input_str):
                return True
        # 通用音频平台 URL 模式
        if any(k in input_str for k in ['podcast', 'audio', 'episode']):
            return True
        return False

    def fetch(self, input_str: str, output_dir: str = "") -> FetchResult:
        if not output_dir:
            output_dir = str(
                Path(__file__).resolve().parent.parent.parent / 'output' / 'audio'
            )
        os.makedirs(output_dir, exist_ok=True)

        platform = self._detect_platform(input_str)
        audio_path = None
        title = ""

        # 策略 1: yt-dlp 通用提取
        logger.info(f"音频平台: yt-dlp 尝试 {input_str}")
        path = _run_ytdlp_download(input_str, output_dir)
        if path:
            audio_path = path
            title = os.path.splitext(os.path.basename(path))[0]
            logger.info(f"音频平台: yt-dlp 成功")

        # 策略 2: 平台专用 API
        if not audio_path:
            if platform == 'xiaoyuzhou':
                logger.info("音频平台: 小宇宙 API 提取...")
                r = _download_xiaoyuzhou_episode(input_str, output_dir)
                if r:
                    audio_path, title = r
                    logger.info("音频平台: 小宇宙 API 成功")
            elif platform == 'lizhi':
                logger.info("音频平台: 荔枝FM API 提取...")
                m = re.search(r'lizhi\.fm/(?:episode/)?(\d+)', input_str)
                if m:
                    r = _download_lizhi(m.group(1), output_dir)
                    if r:
                        audio_path, title = r
                        logger.info("音频平台: 荔枝FM API 成功")
            elif platform == 'ximalaya':
                if '/album/' in input_str:
                    return FetchResult(
                        error="喜马拉雅专辑链接不支持，请使用单集 /track/ 链接",
                    )
                return FetchResult(
                    error="yt-dlp 不支持该喜马拉雅链接，可能是付费内容或链接格式有误",
                )

        if not audio_path or not os.path.exists(audio_path):
            return FetchResult(error="所有下载策略均失败")

        return FetchResult(
            audio_path=audio_path,
            title=title,
            source_type='audio_platform',
            metadata={'platform': platform, 'url': input_str},
        )

    def _detect_platform(self, url: str) -> str:
        for name, pat in self._PLATFORMS.items():
            if re.search(pat, url):
                return name
        return 'unknown'

    @property
    def name(self) -> str:
        return "AudioPlatformSource"


def _download_xiaoyhou_episode(url: str, out_dir: str) -> Optional[Tuple[str, str]]:
    """小宇宙 URL 提取 episode ID 后下载"""
    m = re.search(r'xiaoyuzhoufm\.com/episode/([a-f0-9]+)', url)
    if not m:
        return None
    return _download_xiaoyuzhou(m.group(1), out_dir)
