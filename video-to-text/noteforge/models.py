# -*- coding: utf-8 -*-
"""
NoteForge 数据模型
共享数据结构 — 叶子节点，无业务依赖
"""

from dataclasses import dataclass, field, asdict
from typing import Optional

from noteforge.context import PipelineContext


@dataclass
class GenerationResult:
    """单次生成结果"""
    transcript_path: str
    note_path: str = ""
    quality_report_path: str = ""
    total_score: float = 0.0
    overall_passed: bool = False
    attempts: int = 0
    duration_seconds: float = 0.0
    token_usage: dict = field(default_factory=dict)
    error: Optional[str] = None
    # 内容缓存：SaveStage 失败时仍可通过此字段访问已生成的文本
    note_text: str = ""
    formatted_text: str = ""

    @classmethod
    def from_context(cls, ctx: 'PipelineContext', note_path: str = "") -> 'GenerationResult':
        """从 PipelineContext 构建 GenerationResult（替代手动逐字段复制）。"""
        return cls(
            transcript_path=ctx.transcript_path,
            note_path=note_path,
            note_text=ctx.note_text or "",
            formatted_text=ctx.formatted_text or "",
            total_score=ctx.total_score,
            overall_passed=ctx.overall_passed,
            attempts=getattr(ctx, 'attempts', 0),
            token_usage=getattr(ctx, 'token_usage', {}),
            quality_report_path=getattr(ctx, 'quality_report', {}).get('_report_path', '') if getattr(ctx, 'quality_report', None) else '',
            error=ctx.error,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        # note_text / formatted_text 是运行时缓存，不序列化（可能很大）
        d.pop('note_text', None)
        d.pop('formatted_text', None)
        return d
