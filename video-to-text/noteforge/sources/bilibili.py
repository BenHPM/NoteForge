# -*- coding: utf-8 -*-
"""
NoteForge Bilibili 数据源

封装 download_bilibili 为 Source 实现，同时保留旧版 download_bilibili 函数向后兼容。
"""

import os
import re
import json
import logging
import urllib.request
from pathlib import Path
from typing import Optional, Dict

from noteforge.sources.base import Source, FetchResult

logger = logging.getLogger('noteforge.sources.bilibili')

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _get_temp_dir() -> Path:
    temp_dir = _BASE_DIR / "temp"
    temp_dir.mkdir(exist_ok=True)
    return temp_dir


def normalize_url(url_or_bvid: str) -> str:
    """规范化 URL：BV 号自动补全为完整 URL"""
    if url_or_bvid.startswith("BV"):
        return f"https://www.bilibili.com/video/{url_or_bvid}"
    if "b23.tv" in url_or_bvid:
        try:
            req = urllib.request.Request(
                url_or_bvid,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Referer": "https://www.bilibili.com",
                },
            )
            resp = urllib.request.urlopen(req, timeout=15)
            real_url = resp.geturl()
            if 'bilibili.com/video/' in real_url:
                return real_url
        except Exception:
            pass
    return url_or_bvid


def extract_bvid(url: str) -> str:
    """从 URL 提取 BV 号（支持 b23.tv 短链接）"""
    m = re.search(r'(BV[a-zA-Z0-9]+)', url)
    if m:
        return m.group(1)
    # 短链接 fallback：解析重定向获取真实 URL
    try:
        real_url = normalize_url(url)
        m = re.search(r'(BV[a-zA-Z0-9]+)', real_url)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _api_get(url: str, headers: dict = None) -> dict:
    """GET 请求并解析 JSON"""
    hdrs = {"User-Agent": _USER_AGENT, "Referer": "https://www.bilibili.com"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _find_cookies_file() -> str:
    import glob
    for search_dir in [_get_temp_dir(), _BASE_DIR]:
        candidates = glob.glob(str(search_dir / "cookies*.txt"))
        if not candidates:
            continue
        for c in candidates:
            if os.path.basename(c) == "cookies_all.txt":
                return c
        for c in candidates:
            try:
                with open(c, 'r', encoding='utf-8', errors='replace') as f:
                    if 'bilibili.com' in f.read(5000):
                        return c
            except Exception:
                continue
        return candidates[0]
    return ""


def _download_audio_stream(url: str, output_path: str) -> bool:
    """下载音频流到文件"""
    tmp = output_path + '.tmp'
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Referer": "https://www.bilibili.com",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get('Content-Length', 0))
            downloaded = 0
            with open(tmp, 'wb') as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, output_path)
            return True
        if os.path.exists(tmp):
            os.unlink(tmp)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass
    return False


def _try_ytdlp_bili(url: str, output_path: str) -> bool:
    """尝试 yt-dlp 下载（需要 cookies）"""
    cookies_path = _find_cookies_file()
    cmd = ["yt-dlp", "--no-update", "--extract-audio", "--audio-format", "m4a",
           "-o", output_path]
    if cookies_path:
        cmd.extend(["--cookies", cookies_path])
    else:
        cmd.extend(["--cookies-from-browser", "edge"])
    try:
        r = __import__('subprocess').run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(output_path):
            return True
    except Exception:
        pass
    return False


# ================================================================
# BilibiliSource — Source 实现
# ================================================================

class BilibiliSource:
    """Bilibili 视频数据源"""

    def can_handle(self, input_str: str) -> bool:
        if not input_str:
            return False
        return ('bilibili.com/video/' in input_str or
                input_str.startswith('BV') or
                'b23.tv' in input_str)

    def fetch(self, input_str: str, output_dir: str = "") -> FetchResult:
        url = normalize_url(input_str)
        bvid = extract_bvid(url)
        if not bvid:
            return FetchResult(error=f"无法提取 BV 号: {url}")

        try:
            info = _api_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
            if info.get('code') != 0:
                return FetchResult(error=f"Bilibili API 错误: {info.get('message', info)}")
            data = info['data']
        except Exception as e:
            return FetchResult(error=f"获取视频信息失败: {e}")

        title = data.get('title', bvid)
        duration = data.get('duration', 0)
        cid = data.get('cid', 0)

        if not output_dir:
            output_dir = str(_get_temp_dir())
        os.makedirs(output_dir, exist_ok=True)
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)
        output_path = os.path.join(output_dir, f"{safe_title}.m4a")

        # 已有文件大小校验
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            min_expected = max(duration * 1024, 10240)
            if file_size >= min_expected:
                return FetchResult(
                    audio_path=output_path, title=title, source_type='bilibili',
                    metadata={'bvid': bvid, 'duration': duration, 'method': 'cached'},
                )

        # 策略 1: yt-dlp
        if _try_ytdlp_bili(url, output_path):
            return FetchResult(
                audio_path=output_path, title=title, source_type='bilibili',
                metadata={'bvid': bvid, 'duration': duration, 'method': 'yt-dlp'},
            )

        # 策略 2: Bilibili API
        try:
            play_url = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&fnval=16&qn=64"
            play_data = _api_get(play_url)
            if play_data.get('code') != 0:
                return FetchResult(error=f"播放地址获取失败: {play_data.get('message')}")
            streams = play_data['data'].get('dash', {}).get('audio', [])
            if not streams:
                return FetchResult(error="未找到音频流")
            audio_url = streams[0]['baseUrl']
            if _download_audio_stream(audio_url, output_path):
                return FetchResult(
                    audio_path=output_path, title=title, source_type='bilibili',
                    metadata={'bvid': bvid, 'duration': duration, 'method': 'bilibili-api'},
                )
        except Exception as e:
            logger.error(f"Bilibili API 下载失败: {e}")

        return FetchResult(error="所有下载策略均失败")

    @property
    def name(self) -> str:
        return "BilibiliSource"


# ================================================================
# download_bilibili — 旧版函数（向后兼容）
# ================================================================

def download_bilibili(url_or_bvid: str, output_path: str = None) -> dict:
    """下载 Bilibili 视频音频（旧版 API，保持兼容）"""
    source = BilibiliSource()
    result = source.fetch(url_or_bvid, output_dir=output_path or str(_get_temp_dir()))
    if result.error:
        return {"success": False, "error": result.error}
    return {
        "success": True,
        "path": result.audio_path,
        "title": result.title,
        "duration": result.metadata.get('duration', 0),
        "method": result.metadata.get('method', 'unknown'),
    }
