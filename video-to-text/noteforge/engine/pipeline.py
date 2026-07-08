# -*- coding: utf-8 -*-
"""
NoteForge Pipeline 编排器

将 generate_note 主流程拆分为可组合的阶段，
engine 通过 Pipeline 编排各阶段的执行顺序。
"""

import logging
from typing import List

from noteforge.context import PipelineContext

logger = logging.getLogger('noteforge.engine.pipeline')


class Pipeline:
    """流水线编排器 — 按顺序执行各阶段"""

    def __init__(self, stages=None):
        """
        Args:
            stages: PipelineStage 列表（按执行顺序）
        """
        self._stages = stages or []

    def add_stage(self, stage) -> None:
        """添加阶段"""
        self._stages.append(stage)

    def run(self, ctx: PipelineContext) -> PipelineContext:
        """
        按顺序执行所有阶段

        Args:
            ctx: 流水线上下文

        Returns:
            执行后的上下文
        """
        for stage in self._stages:
            stage_name = stage.name
            logger.info(f"[{stage_name}] 开始执行...")
            try:
                ctx = stage.execute(ctx)
            except Exception as e:
                ctx.error = f"[{stage_name}] {e}"
                logger.error(f"[{stage_name}] 执行失败: {e}", exc_info=True)
                break
            if ctx.error:
                logger.warning(f"[{stage_name}] 阶段报告错误: {ctx.error}")
                break
            logger.info(f"[{stage_name}] 完成")
        return ctx

    @property
    def stage_names(self) -> List[str]:
        """所有阶段名称"""
        return [s.name for s in self._stages]
