# -*- coding: utf-8 -*-
"""
NoteForge — 智能笔记锻造系统

视频/音频/播客 → ASR 转录 → LLM 笔记生成 → R0-R12 质量门禁 → 知识合成 → 飞书同步

注意：此文件保持轻量，仅导出顶层类和常量。
重型引擎模块（LLM/pipeline/飞书）由调用方按需导入，不在此触发。
"""

__version__ = '5.3.0'

# 仅导入真正轻量、无副作用的模块
from noteforge.models import GenerationResult
from noteforge.context import PipelineContext

# 其他符号通过 noteforge.xxx 直接导入（不在此 re-export）
# 如需在交互式环境便捷使用，手动: from noteforge.engine import LLMNoteEngine
