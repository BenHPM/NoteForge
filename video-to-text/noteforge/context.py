# -*- coding: utf-8 -*-
"""
NoteForge PipelineContext — 贯穿全流程的状态

CLI 和各 pipeline 阶段之间通过此 dataclass 传参，
替代直接修改 engine 内部属性（engine._xxx = ...）。
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict
from pathlib import Path


@dataclass
class PathConfig:
    """共享路径配置 — 各子组件从此读取路径，不再各自持有独立路径属性。

    当 engine.configure() 修改 PathConfig 字段时，所有持有引用的子组件
    自动获取新路径，无需手动同步。
    """

    base_dir: Path
    transcripts_dir: Path
    notes_dir: Path
    reports_dir: Path
    logs_dir: Path


@dataclass
class PipelineContext:
    """流水线上下文 — 阶段之间追加/修改，不修改上游结果"""

    # === 输入（阶段 0 设置）===
    source_path: str = ""              # 原始输入（文件路径 / URL）
    output_path: str = ""              # 目标笔记路径
    title: str = ""                    # 笔记标题
    content_type: str = "lecture"      # 内容类型
    mode: str = "notes"                # 生成模式
    force: bool = False                # 是否覆盖
    with_context: bool = False         # 是否注入上下文
    context_limit: int = 3             # 上下文数量上限
    context_prefix: str = ""           # 关联笔记上下文前缀

    # === 阶段产出（各阶段追加）===
    transcript_path: str = ""          # 转写文件路径
    raw_text: str = ""                 # 原始转写文本
    clean_text: str = ""               # 清洗后文本
    chunks: List[str] = field(default_factory=list)
    note_text: str = ""                # LLM 生成结果
    formatted_text: str = ""           # 格式化结果
    quality_report: Optional[Dict] = None
    total_score: float = 0.0
    overall_passed: bool = False
    structural_issues: List[str] = field(default_factory=list)

    # === 元数据（全程累积）===
    attempts: int = 0
    token_usage: Dict = field(default_factory=dict)
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    # === 便利属性 ===
    @property
    def is_audio_source(self) -> bool:
        """输入是否是音频/视频文件"""
        ext = Path(self.source_path).suffix.lower()
        return ext in {'.mp3', '.wav', '.m4a', '.flac', '.mp4', '.mkv', '.avi', '.mov'}

    @property
    def source_stem(self) -> str:
        """输入文件的 stem（无扩展名）"""
        return Path(self.source_path).stem
