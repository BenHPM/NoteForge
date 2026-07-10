# -*- coding: utf-8 -*-
"""NoteForge 质量评估层

入口：
  QualityGate.evaluate(paths)       — 文件路径入口（向后兼容）
  QualityGate.evaluate_text(texts)  — 纯文本入口（推荐）
  QualityGate.evaluate_rule(id, texts) — 单规则调试入口
  python -m noteforge.quality       — CLI 评测入口
"""

from noteforge.quality.models import Issue, RuleResult, LLMEvalResult, QualityReport
from noteforge.quality.gate import QualityGate
from noteforge.quality.heuristics import QualityMetrics, compute_metrics
from noteforge.quality.report import generate_markdown_report
from noteforge.quality.manager import QualityManager
