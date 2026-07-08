# -*- coding: utf-8 -*-
"""
NoteForge — 智能笔记锻造系统

视频/音频/播客 → ASR 转录 → LLM 笔记生成 → R0-R12 质量门禁 → 知识合成 → 飞书同步
"""

__version__ = '5.0.0'

# 顶层便捷 re-export
from noteforge.models import GenerationResult
from noteforge.context import PipelineContext
from noteforge.engine.note_engine import LLMNoteEngine
from noteforge.quality.gate import QualityGate
from noteforge.quality.models import Issue, RuleResult, LLMEvalResult, QualityReport
from noteforge.quality.heuristics import QualityMetrics, compute_metrics
from noteforge.intelligence.synthesis import SynthesisEngine
from noteforge.sources.base import Source, SourceRegistry, FetchResult
