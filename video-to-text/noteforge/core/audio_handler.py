# -*- coding: utf-8 -*-
"""
NoteForge 音频处理模块
提取自 llm_note_engine.py 的音频转写、标题提取、转写文件查找逻辑
"""

import sys
import re
import json
import time
import subprocess
from pathlib import Path
from typing import Optional

from noteforge.models import GenerationResult

# ASR 模块路径（python -m 方式调用）
ASR_MODULE = 'noteforge.sources.asr'


class AudioHandler:
    """音频转写与标题提取处理器"""

    def __init__(self, transcripts_dir, base_dir, logger):
        """
        Args:
            transcripts_dir: 转写文件目录 (Path)
            base_dir: 项目根目录 (Path)
            logger: 日志记录器
        """
        self._transcripts_dir = transcripts_dir
        self._base_dir = base_dir
        self.logger = logger

    def transcribe_audio(self, audio_path: str,
                         result: GenerationResult,
                         force_retranscribe: bool = False) -> Optional[str]:
        """
        使用 Paraformer 转写音频/视频文件

        Args:
            audio_path: 音频/视频文件路径
            result: 用于记录状态的 GenerationResult
            force_retranscribe: 是否强制重新转写（忽略已有缓存）

        Returns:
            转写文本文件路径，或 None（失败）
        """
        stem = Path(audio_path).stem
        transcript_path = self._transcripts_dir / f"{stem}.txt"

        # 如果已有转写文本且不强制重转，直接使用
        if transcript_path.exists() and not force_retranscribe:
            self.logger.info(f"已有转写文本，跳过转写: {transcript_path}")
            return str(transcript_path)

        self.logger.info(f"开始转写音频: {audio_path}")

        # 调用 noteforge.sources.asr（python -m 方式）
        # 平台自适应：Windows 用 python.exe，Unix 用 bin/python
        if sys.platform == 'win32':
            python_exe = str(self._base_dir / "envs" / "paraformer" / "python.exe")
        else:
            python_exe = str(self._base_dir / "envs" / "paraformer" / "bin" / "python")

        if not Path(python_exe).exists():
            # 回退到当前 Python
            python_exe = sys.executable

        try:
            cmd = [python_exe, '-m', ASR_MODULE, audio_path]
            self.logger.info(f"执行: {' '.join(cmd)}")
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                encoding='utf-8', errors='replace',
                cwd=str(self._base_dir),
            )

            # 进度提示：ASR 转写通常需要 4-8 分钟，轮询避免用户以为卡死
            elapsed = 0
            while proc.poll() is None:
                time.sleep(10)
                elapsed += 10
                self.logger.info(f"转写进行中... ({elapsed}秒)")
                if elapsed >= 1800:  # 30 分钟超时
                    proc.kill()
                    self.logger.error("转写超时（30 分钟）")
                    return None

            stdout, stderr = proc.communicate()

            if proc.returncode != 0:
                self.logger.error(f"转写失败: {stderr[:500]}")
                return None

            self.logger.info(f"转写输出: {stdout[-200:]}")

        except Exception as e:
            self.logger.error(f"转写异常: {e}")
            return None

        # 检查输出文件
        if transcript_path.exists():
            self.logger.info(f"转写完成: {transcript_path}")
            return str(transcript_path)

        self.logger.error(f"转写完成但未找到输出文件: {transcript_path}")
        return None

    def extract_title(self, transcript_path: str) -> str:
        """从文件名提取标题（支持 video-mapping.json + 音频文件名回退）"""
        stem = Path(transcript_path).stem

        # 尝试从 video-mapping.json 获取标题
        config_path = self._base_dir / "config" / "video-mapping.json"
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                # 尝试解析 JSON（处理特殊引号）
                import re as _re
                # 替换中文引号为标准引号
                content = _re.sub(r'["""]', '"', content)
                mapping = json.loads(content)
                # 支持数组格式和对象格式
                if isinstance(mapping, list):
                    # 精确匹配
                    for item in mapping:
                        if item.get('id', '') == stem:
                            return item.get('title', stem)
                    # 前缀匹配（ep08 匹配 ep08-theory）
                    for item in mapping:
                        if item.get('id', '').startswith(stem + '-'):
                            return item.get('title', stem)
                elif isinstance(mapping, dict):
                    episode = mapping.get('episodes', {}).get(stem, {})
                    title = episode.get('title', '')
                    if title:
                        return title
            except Exception as e:
                self.logger.debug(f"读取 video-mapping.json 失败: {e}")

        # 回退1：在 temp/ 和 output/audio/ 中查找同 stem 的中文音频文件名
        # 解决 ASCII 文件名（如 quant_career_v2）与中文标题的映射问题
        audio_exts = {'.m4a', '.mp3', '.wav', '.flac'}
        for search_dir in [self._base_dir / 'temp', self._base_dir / 'output' / 'audio']:
            if not search_dir.exists():
                continue
            for f in search_dir.iterdir():
                if f.suffix.lower() in audio_exts:
                    # 去掉扩展名，比较 stem
                    audio_stem = f.stem
                    if audio_stem == stem:
                        # 找到同名音频文件，但可能包含中文
                        if any(ord(c) > 127 for c in audio_stem):
                            return audio_stem

        # 回退2：在 transcripts/ 中查找同 stem 的中文转录文件名
        if self._transcripts_dir.exists():
            for f in self._transcripts_dir.iterdir():
                if f.suffix == '.txt' and f.stem != stem:
                    # 检查是否有中文同义文件（同大小的文件可能是同一内容的中文命名版本）
                    t_path = Path(transcript_path)
                    if f.stat().st_size == t_path.stat().st_size and any(ord(c) > 127 for c in f.stem):
                        return f.stem

        return stem

    def find_transcript_for_note(self, note_path: str) -> Optional[str]:
        """为笔记文件找到对应的转写文件"""
        stem = Path(note_path).stem

        # 1. 直接匹配
        candidate = self._transcripts_dir / f"{stem}.txt"
        if candidate.exists():
            return str(candidate)

        # 2. 在笔记目录的父目录找
        candidate = Path(note_path).parent.parent / "transcripts" / f"{stem}.txt"
        if candidate.exists():
            return str(candidate)

        # 3. 通过 video-mapping.json 反查（中文标题 → epXX）
        config_path = self._base_dir / "config" / "video-mapping.json"
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    mapping = json.load(f)
                if isinstance(mapping, list):
                    for item in mapping:
                        title = item.get('title', '')
                        if title:
                            # 模糊匹配：去掉标点后比较
                            import unicodedata
                            def _normalize(s):
                                s = re.sub(r'[：:、，,。.！!？?\-\s]', '', s)
                                return s
                            t_norm = _normalize(title)
                            s_norm = _normalize(stem)
                            if t_norm == s_norm or t_norm in s_norm or s_norm in t_norm:
                                filename = item.get('filename', '')
                                if filename:
                                    t_stem = Path(filename).stem
                                    t_path = self._transcripts_dir / f"{t_stem}.txt"
                                    if t_path.exists():
                                        return str(t_path)
            except Exception as e:
                self.logger.debug(f"video-mapping.json 匹配转写文件失败: {e}")

        # 4. 模糊匹配：在 transcripts 目录中搜索包含 stem 关键词的文件
        if self._transcripts_dir.exists():
            for t_file in sorted(self._transcripts_dir.glob('*.txt')):
                if t_file.stem in stem or stem in t_file.stem:
                    return str(t_file)

        return None
