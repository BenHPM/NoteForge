# -*- coding: utf-8 -*-
"""
NoteForge 音频平台下载策略

从 cli.py 提取的 MediaDownloader — yt-dlp + 平台 API 降级。
归入 sources 层，职责是"数据源获取"而非"CLI 入口"。
"""

import os
import re
import json
import subprocess


class MediaDownloader:
    """音频平台下载策略（yt-dlp + 平台 API 降级）"""

    @staticmethod
    def try_ytdlp(url, out_dir):
        """尝试 yt-dlp 下载，返回 audio_path 或 None"""
        import shutil
        if not shutil.which('yt-dlp'):
            return None
        output_tpl = os.path.join(out_dir, '%(title)s.%(ext)s')
        dl_cmd = [
            "yt-dlp", "--no-update",
            "--extract-audio", "--audio-format", "mp3",
            "--no-playlist", "-o", output_tpl, url,
        ]
        dl = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=600)
        if dl.returncode != 0:
            return None
        for line in (dl.stdout + dl.stderr).splitlines():
            if '[ExtractAudio]' in line and 'Destination:' in line:
                p = line.split('Destination:', 1)[1].strip()
                if os.path.exists(p):
                    return p
        # 回退：找最新 mp3
        import glob as _glob
        candidates = _glob.glob(os.path.join(out_dir, '*.mp3'))
        return max(candidates, key=os.path.getmtime) if candidates else None

    @staticmethod
    def try_xiaoyuzhou(url, out_dir):
        """小宇宙 API 提取，返回 (audio_path, title) 或 None"""
        import urllib.request
        m = re.search(r'xiaoyuzhoufm\.com/episode/([a-f0-9]+)', url)
        if not m:
            return None
        eid = m.group(1)
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

    @staticmethod
    def try_lizhi(url, out_dir):
        """荔枝FM API 提取，返回 (audio_path, title) 或 None"""
        import urllib.request
        m = re.search(r'lizhi\.fm/(?:episode/)?(\d+)', url)
        if not m:
            return None
        ep_id = m.group(1)
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
