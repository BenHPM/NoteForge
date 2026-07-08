# -*- coding: utf-8 -*-
"""
NoteForge Format Stage — 笔记格式化 + 结构校验

从 note_engine.py generate_note() Step 5-6 提取：
- 格式化输出（NoteFormatter.format）
- 结构校验（NoteFormatter.validate_structure）

从 PipelineContext 读取：note_text, title, transcript_path, content_type, mode
写入 PipelineContext：formatted_text, structural_issues, warnings
"""

import logging
from typing import Optional

from noteforge.context import PipelineContext
from noteforge.engine.stages.base import PipelineStage
from noteforge.core.note_formatter import NoteFormatter


class FormatStage(PipelineStage):
    """
    格式化阶段 — 笔记后处理 + 结构校验

    从 PipelineContext 读取:
      - note_text: LLM 生成的原始笔记
      - title: 笔记标题
      - transcript_path: 转写文件路径
      - content_type: 内容类型
      - mode: 生成模式
      - clean_text: 清洗后转写文本（用于质量声明）

    写入 PipelineContext:
      - formatted_text: 格式化后的笔记
      - structural_issues: 结构问题列表
      - warnings: 追加警告
    """

    def __init__(self,
                 formatter: NoteFormatter,
                 content_type: str = 'lecture',
                 logger: Optional[logging.Logger] = None):
        """
        Args:
            formatter: NoteFormatter 实例
            content_type: 内容类型
            logger: 日志记录器
        """
        self.formatter = formatter
        self.content_type = content_type
        self.logger = logger or logging.getLogger('noteforge.engine.stages.format')

    @property
    def name(self) -> str:
        return "format"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        """执行格式化阶段"""
        # Step 5: 格式化输出
        formatted = self.formatter.format(
            ctx.note_text,
            ctx.title,
            ctx.transcript_path,
            mode=ctx.mode,
            content_type=self.content_type or ctx.content_type,
            transcript_text=ctx.clean_text
        )
        ctx.formatted_text = formatted

        # Step 6: 结构校验
        structural_issues = self.formatter.validate_structure(
            formatted,
            mode=ctx.mode,
            content_type=self.content_type or ctx.content_type
        )
        ctx.structural_issues = structural_issues
        if structural_issues:
            self.logger.warning(
                f"笔记结构问题: {'; '.join(structural_issues)}"
            )

        return ctx
