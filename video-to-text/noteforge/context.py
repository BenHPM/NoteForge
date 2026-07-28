# -*- coding: utf-8 -*-
"""
NoteForge PipelineContext — 贯穿全流程的状态

CLI 和各 pipeline 阶段之间通过此 dataclass 传参，
替代直接修改 engine 内部属性（engine._xxx = ...)。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any
from pathlib import Path
import uuid


# ============================================================
# 阶段错误类型
# ============================================================

class StageErrorKind(str, Enum):
    """阶段错误分类"""
    FATAL = "fatal"          # 不可恢复，pipeline 停止
    RETRYABLE = "retryable"  # 可重试（但当前策略决定不重试）
    SKIP = "skip"            # 跳过此项（非错误，如已存在）
    WARNING = "warning"      # 警告，不阻断流程


@dataclass
class StageError:
    """结构化阶段错误（替代 ctx.error: Optional[str]）"""

    stage: str                    # 出错阶段名称
    message: str                  # 错误描述
    kind: StageErrorKind = StageErrorKind.FATAL
    detail: Optional[Dict[str, Any]] = None

    def __str__(self) -> str:
        return f"[{self.stage}] {self.message}"

    def __contains__(self, item: str) -> bool:
        """支持 'substring' in error 的向后兼容写法。"""
        return item in self.message or item in self.stage

    @classmethod
    def from_string(cls, stage: str, error_str: str) -> "StageError":
        """从旧式字符串错误构造（向后兼容）。"""
        return cls(stage=stage, message=error_str, kind=StageErrorKind.FATAL)


# ============================================================
# PathConfig
# ============================================================

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
    _audio_dir: Optional[Path] = None

    @property
    def audio_dir(self) -> Path:
        if self._audio_dir is None:
            self._audio_dir = self.base_dir / 'output' / 'audio'
        return self._audio_dir

    @audio_dir.setter
    def audio_dir(self, value: Path) -> None:
        self._audio_dir = value


# ============================================================
# PipelineContext
# ============================================================

@dataclass
class PipelineContext:
    """流水线上下文 — 阶段之间追加/修改，不修改上游结果

    分组视图：
      ctx.inputs  → 输入参数（source_path, title, content_type, ...）
      ctx.outputs → 阶段产出（note_text, formatted_text, quality_report, ...）
      ctx.meta    → 运行元数据（attempts, token_usage, error, warnings）
    """

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
    batch_mode: bool = False           # 批量模式

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
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    attempts: int = 0
    token_usage: Dict = field(default_factory=dict)
    error: Optional[StageError] = None
    warnings: List[str] = field(default_factory=list)

    # === 分组视图 ===

    @property
    def inputs(self) -> Dict[str, Any]:
        """输入参数分组（只读快照）"""
        return {
            'source_path': self.source_path,
            'output_path': self.output_path,
            'title': self.title,
            'content_type': self.content_type,
            'mode': self.mode,
            'force': self.force,
            'with_context': self.with_context,
            'context_limit': self.context_limit,
            'context_prefix': self.context_prefix,
            'batch_mode': self.batch_mode,
            'transcript_path': self.transcript_path,
        }

    @property
    def outputs(self) -> Dict[str, Any]:
        """阶段产出分组（只读快照）"""
        return {
            'raw_text': self.raw_text,
            'clean_text': self.clean_text,
            'chunks': self.chunks,
            'note_text': self.note_text,
            'formatted_text': self.formatted_text,
            'quality_report': self.quality_report,
            'total_score': self.total_score,
            'overall_passed': self.overall_passed,
            'structural_issues': self.structural_issues,
        }

    @property
    def meta(self) -> Dict[str, Any]:
        """运行元数据分组（只读快照）"""
        return {
            'trace_id': self.trace_id,
            'attempts': self.attempts,
            'token_usage': self.token_usage,
            'error': self.error,
            'warnings': self.warnings,
        }

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
