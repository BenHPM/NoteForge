# -*- coding: utf-8 -*-
"""
NoteForge 音频处理模块
提取自 llm_note_engine.py 的音频转写、标题提取、转写文件查找逻辑
"""

import os
import sys
import re
import json
import time
import subprocess
import shutil
import uuid
from pathlib import Path
from typing import Optional

from noteforge.models import GenerationResult

# ASR 脚本路径（直接执行，绕过 python -m 的 import 缓存）
ASR_SCRIPT = str(Path(__file__).parent.parent / 'sources' / 'asr.py')


def _probe_duration_seconds(path: str) -> float:
    """用 ffprobe 获取音频/视频时长（秒），失败返回 0.0"""
    try:
        result = subprocess.run(
            [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            val = result.stdout.strip()
            if val:
                return max(0.0, float(val))
    except Exception:
        pass
    return 0.0


class AudioHandler:
    """音频转写与标题提取处理器"""

    def __init__(self, transcripts_dir, base_dir, logger):
        self._transcripts_dir = transcripts_dir
        self._base_dir = base_dir
        self.logger = logger

    def transcribe_audio(self, audio_path: str,
                         result: GenerationResult,
                         force_retranscribe: bool = False) -> Optional[str]:
        stem = Path(audio_path).stem
        transcript_path = self._transcripts_dir / f"{stem}.txt"

        if transcript_path.exists() and not force_retranscribe:
            self.logger.info(f"已有转写文本，跳过转写: {transcript_path}")
            return str(transcript_path)

        self.logger.info(f"开始转写音频: {audio_path}")

        if sys.platform == 'win32':
            python_exe = str(self._base_dir / "envs" / "paraformer" / "python.exe")
        else:
            python_exe = str(self._base_dir / "envs" / "paraformer" / "bin" / "python")
        if not Path(python_exe).exists():
            python_exe = sys.executable

        # 中文文件名可能导致 ASR 子进程路径处理异常，复制到纯 ASCII 临时文件名
        temp_dir = self._base_dir / "temp" / "_asr"
        temp_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"asr_{int(time.time())}_{uuid.uuid4().hex[:8]}{Path(audio_path).suffix}"
        safe_path = temp_dir / safe_name
        _temp_created = False

        try:
            # 复制到安全文件名（可能因文件不存在而失败，统一在 try 内处理）
            if not safe_path.exists():
                shutil.copy2(audio_path, safe_path)
                self.logger.debug(f"复制到安全文件名: {safe_path}")
                _temp_created = True
            asr_input_path = str(safe_path)

            # 构建子进程环境，确保 FunASR 能找到模型缓存
            subprocess_env = os.environ.copy()
            if sys.platform == 'win32':
                _home = subprocess_env.get('USERPROFILE') or subprocess_env.get('HOME', '')
                if _home:
                    subprocess_env.setdefault('HOME', _home)

            # 直接指定输出路径，避免 safe 文件名与引擎期望的 stem 不匹配
            cmd = [python_exe, ASR_SCRIPT, asr_input_path, '--output', str(transcript_path)]
            self.logger.info(f"执行: {' '.join(cmd)}")

            # 通过 ffprobe 获取真实音频时长（比文件大小估算准确得多）
            # 111MB m4a 的时长差异极大：1小时访谈 vs 3小时访谈，不能靠大小猜
            duration_secs = _probe_duration_seconds(asr_input_path)
            if duration_secs <= 0:
                # ffprobe 不可用，回退到大小估算
                file_mb = os.path.getsize(asr_input_path) / (1024 * 1024)
                est_wav_mb = file_mb / 10 if asr_input_path.lower().endswith('.m4a') else file_mb
                duration_secs = max(60, est_wav_mb * 6 * 60)  # 10MB wav ≈ 1min
                self.logger.info(f"ASR 时长（估算）: ~{int(duration_secs/60)}分钟 ({file_mb:.0f}MB)")
            else:
                mins, secs = divmod(int(duration_secs), 60)
                self.logger.info(f"ASR 时长: {mins}分{secs}秒")
            # 宽松超时：模型加载(~60s) + ffmpeg转码(~2-5min) + 识别(1.5x时长) + 余量
            timeout_s = max(3600, int(duration_secs * 2.0) + 600)
            est_minutes = duration_secs / 60
            self.logger.info(f"ASR 超时: {timeout_s}秒 (~{int(est_minutes)}分钟音频)")

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(self._base_dir),
                    env=subprocess_env,
                )
                stdout, stderr = proc.communicate(timeout=timeout_s)
                result_proc = type('R', (), {})()
                result_proc.returncode = proc.returncode
                result_proc.stdout = stdout.decode('utf-8', errors='replace')
                result_proc.stderr = stderr.decode('utf-8', errors='replace')
            except subprocess.TimeoutExpired:
                self.logger.error(f"ASR 超时 ({timeout_s}秒)，终止进程")
                proc.kill()
                try:
                    proc.communicate(timeout=10)
                except Exception:
                    pass
                return None
            except Exception as e:
                self.logger.error(f"ASR 子进程异常: {e}")
                return None

            if result_proc.returncode != 0:
                self.logger.error(f"转写失败 (exit={result_proc.returncode}):\n{result_proc.stderr[:10000]}")
                return None

            # 从输出确认保存的文件路径
            stdout_text = result_proc.stdout.strip()
            self.logger.debug(f"ASR 输出: {stdout_text[-500:]}")

            if transcript_path.exists():
                self.logger.info(f"转写完成: {transcript_path}")
                return str(transcript_path)

            self.logger.error(f"转写完成但未找到输出文件: {transcript_path}")
            return None

        except Exception as e:
            self.logger.error(f"转写异常: {e}")
            return None
        finally:
            # 清理临时文件，避免磁盘积累拖慢后续 ASR 调用
            if _temp_created and safe_path.exists():
                try:
                    safe_path.unlink()
                    self.logger.debug(f"清理临时ASR文件: {safe_path}")
                except OSError:
                    pass

    def extract_title(self, transcript_path: str) -> str:
        stem = Path(transcript_path).stem
        config_path = self._base_dir / "config" / "video-mapping.json"
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                content = re.sub(r'["""]', '"', content)
                mapping = json.loads(content)
                if isinstance(mapping, list):
                    for item in mapping:
                        if item.get('id', '') == stem:
                            return item.get('title', stem)
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

        audio_exts = {'.m4a', '.mp3', '.wav', '.flac'}
        for search_dir in [self._base_dir / 'temp', self._base_dir / 'output' / 'audio']:
            if not search_dir.exists():
                continue
            for f in search_dir.iterdir():
                if f.suffix.lower() in audio_exts:
                    audio_stem = f.stem
                    if audio_stem == stem and any(ord(c) > 127 for c in audio_stem):
                        return audio_stem

        if self._transcripts_dir.exists():
            for f in self._transcripts_dir.iterdir():
                if f.suffix == '.txt' and f.stem != stem:
                    t_path = Path(transcript_path)
                    if f.stat().st_size == t_path.stat().st_size and any(ord(c) > 127 for c in f.stem):
                        return f.stem

        return stem

    def find_transcript_for_note(self, note_path: str) -> Optional[str]:
        stem = Path(note_path).stem
        candidate = self._transcripts_dir / f"{stem}.txt"
        if candidate.exists():
            return str(candidate)

        candidate = Path(note_path).parent.parent / "transcripts" / f"{stem}.txt"
        if candidate.exists():
            return str(candidate)

        config_path = self._base_dir / "config" / "video-mapping.json"
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    mapping = json.load(f)
                if isinstance(mapping, list):
                    for item in mapping:
                        title = item.get('title', '')
                        if title:
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
                self.logger.debug(f"video-mapping.json 匹配失败: {e}")

        if self._transcripts_dir.exists():
            for t_file in sorted(self._transcripts_dir.glob('*.txt')):
                if t_file.stem in stem or stem in t_file.stem:
                    return str(t_file)

        return None
