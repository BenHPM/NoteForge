"""
NoteForge YouTube 处理模块 v1.0
功能:
- 下载 YouTube 视频音频
- 提取视频元数据（标题、频道、时长）
- 支持播放列表批量下载
- 集成到 llm_note_engine.py 的转写+笔记生成流程

依赖:
- yt-dlp (pip install yt-dlp)
- ffmpeg (已在系统 PATH 中)
"""

import os
import json
import subprocess
import logging
import re
from typing import List, Optional, Dict

logger = logging.getLogger('noteforge.youtube')


class YouTubeError(Exception):
    """YouTube 处理异常"""
    pass


class YouTubeHandler:
    """YouTube 音频下载和元数据提取"""

    def __init__(self, output_dir: str = None, temp_dir: str = None):
        """
        Args:
            output_dir: 音频输出目录
            temp_dir: 临时文件目录
        """
        self.output_dir = output_dir or 'output/audio'
        self.temp_dir = temp_dir or 'temp'
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)

        # 检查 yt-dlp 是否可用
        self._ytdlp_path = self._find_ytdlp()

    def _find_ytdlp(self) -> str:
        """查找 yt-dlp 可执行文件"""
        # 优先使用 yt-dlp 命令
        try:
            result = subprocess.run(
                ['yt-dlp', '--version'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return 'yt-dlp'
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # 尝试 python -m yt_dlp
        try:
            result = subprocess.run(
                ['python', '-m', 'yt_dlp', '--version'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return 'python -m yt_dlp'
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        raise YouTubeError(
            "yt-dlp 未安装。请运行: pip install yt-dlp"
        )

    def download_audio(self, url: str, filename: str = None) -> Dict:
        """
        下载 YouTube 视频的音频

        Args:
            url: YouTube 视频 URL
            filename: 输出文件名（不含扩展名，默认使用视频 ID）

        Returns:
            {
                'path': 音频文件路径,
                'title': 视频标题,
                'channel': 频道名,
                'duration': 时长（秒）,
                'id': 视频 ID,
                'url': 原始 URL
            }
        """
        logger.info(f"下载音频: {url}")

        # 先获取元数据
        metadata = self._extract_metadata(url)
        video_id = metadata.get('id', 'unknown')
        title = metadata.get('title', video_id)

        if filename is None:
            # 清理文件名中的非法字符
            safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)[:80]
            filename = safe_title

        output_path = os.path.join(self.output_dir, f"{filename}.mp3")

        # 如果已下载，跳过
        if os.path.exists(output_path):
            logger.info(f"音频已存在，跳过下载: {output_path}")
            metadata['path'] = output_path
            return metadata

        # 下载音频
        temp_output = os.path.join(self.temp_dir, f"{video_id}.%(ext)s")
        cmd = self._build_ytdlp_cmd([
            '--extract-audio',
            '--audio-format', 'mp3',
            '--audio-quality', '0',  # 最高音质
            '--output', temp_output,
            '--no-playlist',  # 不下载播放列表
            url
        ])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=600  # 10 分钟超时
            )
            if result.returncode != 0:
                raise YouTubeError(
                    f"yt-dlp 下载失败:\n{result.stderr[:500]}"
                )
        except subprocess.TimeoutExpired:
            raise YouTubeError("下载超时（10 分钟）")

        # 找到下载的文件并移动到目标位置
        downloaded = self._find_downloaded_file(temp_output, video_id)
        if downloaded:
            os.rename(downloaded, output_path)
            logger.info(f"音频已保存: {output_path}")
        else:
            raise YouTubeError("下载完成但未找到音频文件")

        metadata['path'] = output_path
        return metadata

    def download_playlist(self, url: str) -> List[Dict]:
        """
        下载 YouTube 播放列表的所有音频

        Args:
            url: YouTube 播放列表 URL

        Returns:
            下载结果列表
        """
        logger.info(f"下载播放列表: {url}")

        # 获取播放列表元数据
        playlist_info = self._extract_playlist_metadata(url)
        results: List[Dict] = []

        for i, entry in enumerate(playlist_info, 1):
            video_url = entry.get('url', '')
            if not video_url:
                continue

            logger.info(f"[{i}/{len(playlist_info)}] {entry.get('title', video_url)}")
            try:
                result = self.download_audio(video_url)
                results.append(result)
            except YouTubeError as e:
                logger.error(f"下载失败: {e}")
                results.append({
                    'url': video_url,
                    'error': str(e)
                })

        return results

    def _extract_metadata(self, url: str) -> Dict:
        """提取单个视频的元数据"""
        cmd = self._build_ytdlp_cmd([
            '--dump-json',
            '--no-download',
            '--no-playlist',
            url
        ])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                logger.warning(f"元数据提取失败: {result.stderr[:200]}")
                return {'id': self._extract_video_id(url), 'url': url}

            data = json.loads(result.stdout)
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
        except (json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            logger.warning(f"元数据解析失败: {e}")
            return {'id': self._extract_video_id(url), 'url': url}

    def _extract_playlist_metadata(self, url: str) -> List[Dict]:
        """提取播放列表元数据"""
        cmd = self._build_ytdlp_cmd([
            '--dump-json',
            '--no-download',
            '--flat-playlist',
            url
        ])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                raise YouTubeError(f"播放列表元数据提取失败: {result.stderr[:300]}")

            entries = []
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return entries
        except subprocess.TimeoutExpired:
            raise YouTubeError("播放列表元数据提取超时")

    def _build_ytdlp_cmd(self, args: List[str]) -> List[str]:
        """构建 yt-dlp 命令"""
        if self._ytdlp_path == 'yt-dlp':
            return ['yt-dlp'] + args
        else:
            return ['python', '-m', 'yt_dlp'] + args

    def _extract_video_id(self, url: str) -> str:
        """从 URL 提取视频 ID"""
        patterns = [
            r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
            r'(?:embed/)([a-zA-Z0-9_-]{11})',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return 'unknown'

    def _find_downloaded_file(self, template: str, video_id: str) -> Optional[str]:
        """找到 yt-dlp 下载的文件"""
        # yt-dlp 输出文件名模板可能被替换
        temp_dir = os.path.dirname(template)
        for f in os.listdir(temp_dir):
            if video_id in f and f.endswith(('.mp3', '.m4a', '.wav', '.opus')):
                return os.path.join(temp_dir, f)
        return None
