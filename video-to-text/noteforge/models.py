# -*- coding: utf-8 -*-
"""
NoteForge 数据模型
共享数据结构 — 叶子节点，无业务依赖
"""

from dataclasses import dataclass, field, asdict
from typing import Optional


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

    def to_dict(self) -> dict:
        return asdict(self)
