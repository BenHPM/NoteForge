# -*- coding: utf-8 -*-
"""
NoteForge 本地文件数据源

处理本地音频/视频文件：
  - 音频（.mp3/.wav/.m4a/.flac）：直接复制到输出目录
  - 视频（.mp4/.mkv/.avi/.mov）：通过 ffmpeg 提取音频
"""

import os
import shutil
import subprocess
import logging
from pathlib import Path

from noteforge.sources.base import Source, FetchResult

logger = logging.getLogger('noteforge.sources.local')

# 支持的音频扩展名（直接复制）
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.flac'}
# 支持的视频扩展名（ffmpeg 提取音频）
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov'}
# 所有支持的扩展名
ALL_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


class LocalSource(Source):
    """本地文件数据源"""

    def can_handle(self, input_str: str) -> bool:
        """
        判断是否为本地支持的媒体文件

        Args:
            input_str: 文件路径

        Returns:
            True 如果文件存在且扩展名在支持的列表中
        """
        if not input_str:
            return False
        ext = Path(input_str).suffix.lower()
        if ext not in ALL_EXTENSIONS:
            return False
        return os.path.isfile(input_str)

    def fetch(self, input_str: str, output_dir: str = "") -> FetchResult:
        """
        获取本地文件：音频复制，视频提取音频

        Args:
            input_str: 本地文件路径
            output_dir: 输出目录（默认与输入文件同目录）

        Returns:
            FetchResult，包含 audio_path、title、source_type
        """
        input_path = Path(input_str)
        ext = input_path.suffix.lower()

        if not input_path.exists():
            return FetchResult(
                audio_path="",
                title="",
                source_type="local",
                error=f"文件不存在: {input_str}",
            )

        # 确保输出目录存在
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
        else:
            output_dir = str(input_path.parent)

        title = input_path.stem

        try:
            if ext in AUDIO_EXTENSIONS:
                return self._handle_audio(input_path, output_dir, title)
            elif ext in VIDEO_EXTENSIONS:
                return self._handle_video(input_path, output_dir, title)
            else:
                return FetchResult(
                    audio_path="",
                    title="",
                    source_type="local",
                    error=f"不支持的扩展名: {ext}",
                )
        except Exception as e:
            logger.error("处理本地文件失败: %s", e)
            return FetchResult(
                audio_path="",
                title="",
                source_type="local",
                error=f"处理失败: {e}",
            )

    def _handle_audio(self, input_path: Path, output_dir: str, title: str) -> FetchResult:
        """处理音频文件：复制到输出目录"""
        # 如果文件已在输出目录，无需复制
        if str(input_path.parent) == output_dir:
            audio_path = str(input_path)
        else:
            output_path = Path(output_dir) / input_path.name
            shutil.copy2(str(input_path), str(output_path))
            audio_path = str(output_path)

        logger.info("本地音频已就位: %s", audio_path)
        return FetchResult(
            audio_path=audio_path,
            title=title,
            source_type="local",
        )

    def _handle_video(self, input_path: Path, output_dir: str, title: str) -> FetchResult:
        """处理视频文件：通过 ffmpeg 提取音频"""
        output_path = Path(output_dir) / f"{title}.wav"
        cmd = [
            'ffmpeg', '-y', '-i', str(input_path),
            '-vn',
            '-acodec', 'pcm_s16le',
            '-ar', '16000',
            '-ac', '1',
            str(output_path),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=300,
        )

        if result.returncode != 0 or not output_path.exists():
            error_msg = result.stderr[-500:] if result.stderr else "ffmpeg 执行失败"
            return FetchResult(
                audio_path="",
                title="",
                source_type="local",
                error=f"ffmpeg 音频提取失败: {error_msg}",
            )

        logger.info("视频音频提取完成: %s", output_path)
        return FetchResult(
            audio_path=str(output_path),
            title=title,
            source_type="local",
        )
