# -*- coding: utf-8 -*-
"""
NoteForge ASR 提供商抽象层

将 ASR 实现与业务逻辑解耦：
- ASRProvider ABC: 统一转写接口 + 健康检查
- LocalParaformerASR: 当前 FunASR 实现（委托 noteforge.sources.asr）
- MockASR: CI/测试用 mock

用法:
    from noteforge.sources.asr_provider import ASRProvider, LocalParaformerASR, MockASR
"""

import os
import sys
import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger('noteforge.asr_provider')


# ---- 数据类 ----

@dataclass
class TranscriptionResult:
    """ASR 转写结果"""
    text: str
    duration_seconds: float = 0.0
    language: str = "zh"
    speaker_count: int = 0
    error: Optional[str] = None


# ---- 异常 ----

class ASRTimeoutError(Exception):
    """ASR 转写超时"""


# ---- ABC ----

class ASRProvider(ABC):
    """ASR 提供商抽象基类"""

    @abstractmethod
    def health_check(self) -> tuple[bool, str]:
        """健康检查

        Returns:
            (is_healthy, diagnostic_message)
        """

    @abstractmethod
    def transcribe(self, audio_path: str, timeout: int = 7200) -> TranscriptionResult:
        """转写音频文件

        Args:
            audio_path: 音频文件路径
            timeout: 超时秒数（默认 7200 = 2 小时）

        Returns:
            TranscriptionResult

        Raises:
            ASRTimeoutError: 转写超时
            FileNotFoundError: 音频文件不存在
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """提供商名称（用于日志）"""


# ---- 具体实现 ----

class LocalParaformerASR(ASRProvider):
    """本地 FunASR Paraformer 实现

    委托 noteforge.sources.asr.transcribe_with_paraformer。
    health_check 验证 Python 3.10 环境存在且 funasr 可导入。
    """

    def __init__(self, python_path: Optional[str] = None):
        """
        Args:
            python_path: Paraformer 环境 python 路径。
                         默认使用 envs/paraformer/python.exe。
        """
        if python_path:
            self._python_path = python_path
        else:
            # 推导默认路径: noteforge/sources/../../envs/paraformer/python.exe
            self._python_path = str(
                Path(__file__).parent.parent.parent / "envs" / "paraformer" / "python.exe"
            )

    @property
    def name(self) -> str:
        return "LocalParaformerASR"

    def health_check(self) -> tuple[bool, str]:
        """验证 Paraformer 环境可用

        检查项:
        1. python.exe 存在
        2. Python 版本为 3.10
        3. funasr 可导入
        """
        # 1. 检查 python.exe 存在
        if not os.path.isfile(self._python_path):
            return False, f"Python 环境不存在: {self._python_path}"

        # 2. 检查 Python 版本
        try:
            result = subprocess.run(
                [self._python_path, "-c", "import sys; print(sys.version)"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return False, f"Python 执行失败: {result.stderr.strip()}"
            version_str = result.stdout.strip()
            if not version_str.startswith("3.10"):
                return False, f"Python 版本不符（需 3.10）: {version_str}"
        except subprocess.TimeoutExpired:
            return False, "Python 版本检查超时"
        except Exception as e:
            return False, f"Python 版本检查异常: {e}"

        # 3. 检查 funasr 可导入
        try:
            result = subprocess.run(
                [self._python_path, "-c", "import funasr; print(funasr.__version__)"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return False, f"funasr 导入失败: {result.stderr.strip()[:200]}"
        except subprocess.TimeoutExpired:
            return False, "funasr 导入检查超时"
        except Exception as e:
            return False, f"funasr 导入检查异常: {e}"

        return True, f"Paraformer 环境正常 (Python {version_str.split()[0]})"

    def transcribe(self, audio_path: str, timeout: int = 7200) -> TranscriptionResult:
        """使用 Paraformer 转写音频

        委托 noteforge.sources.asr.transcribe_with_paraformer。
        """
        if not os.path.isfile(audio_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        from noteforge.sources.asr import transcribe_with_paraformer, _get_audio_duration

        duration = _get_audio_duration(audio_path)

        try:
            text = transcribe_with_paraformer(audio_path)
        except Exception as e:
            error_msg = str(e)
            if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                raise ASRTimeoutError(f"ASR 转写超时 ({timeout}s): {error_msg}") from e
            return TranscriptionResult(
                text="",
                duration_seconds=duration,
                error=error_msg,
            )

        return TranscriptionResult(
            text=text,
            duration_seconds=duration,
        )


class MockASR(ASRProvider):
    """CI/测试用 mock ASR 提供商

    - health_check: 始终健康
    - transcribe: 返回固定中文文本
    """

    _MOCK_TEXT = "这是一段模拟的语音转写文本，用于测试目的。"

    @property
    def name(self) -> str:
        return "MockASR"

    def health_check(self) -> tuple[bool, str]:
        return True, "MockASR 始终健康"

    def transcribe(self, audio_path: str, timeout: int = 7200) -> TranscriptionResult:
        return TranscriptionResult(
            text=self._MOCK_TEXT,
            duration_seconds=10.0,
            language="zh",
            speaker_count=1,
        )
