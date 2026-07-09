# -*- coding: utf-8 -*-
"""
NoteForge 数据源抽象层

统一内容获取接口，消除 CLI 中的硬编码分支。
新增数据源只需实现 Source 接口 + 注册到 SourceRegistry。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class FetchResult:
    """统一获取结果"""
    audio_path: Optional[str] = None       # 下载的音频路径
    transcript_path: Optional[str] = None  # 已有的转写路径
    title: str = ""                        # 内容标题
    source_type: str = ""                  # "youtube" / "bilibili" / "podcast" / "local" / "url"
    error: Optional[str] = None            # 错误信息


class Source(ABC):
    """内容来源抽象基类"""

    @abstractmethod
    def can_handle(self, input_str: str) -> bool:
        """判断是否能处理此输入（URL/路径/ID）"""
        pass

    @abstractmethod
    def fetch(self, input_str: str, output_dir: str = "") -> FetchResult:
        """
        获取内容，返回统一结果

        Args:
            input_str: 输入标识（URL / 文件路径 / BV号 等）
            output_dir: 音频输出目录

        Returns:
            FetchResult
        """
        pass

    @property
    def name(self) -> str:
        """数据源名称"""
        return self.__class__.__name__


class SourceRegistry:
    """URL/文件路由 — 自动选择正确的 Source"""

    def __init__(self):
        self._sources: List[Source] = []

    def register(self, source: Source) -> None:
        """注册数据源"""
        self._sources.append(source)

    def fetch(self, input_str: str, output_dir: str = "") -> FetchResult:
        """
        自动路由到正确的 Source 并获取内容

        Args:
            input_str: 输入标识
            output_dir: 音频输出目录

        Returns:
            FetchResult

        Raises:
            ValueError: 无法识别输入
        """
        for source in self._sources:
            if source.can_handle(input_str):
                return source.fetch(input_str, output_dir)
        raise ValueError(f"无法识别输入: {input_str}")

    def list_sources(self) -> List[str]:
        """列出所有已注册的数据源"""
        return [s.name for s in self._sources]
