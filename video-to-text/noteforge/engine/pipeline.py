# -*- coding: utf-8 -*-
"""
NoteForge Pipeline 编排器

将 generate_note 主流程拆分为可组合的阶段，
engine 通过 Pipeline 编排各阶段的执行顺序。
"""

import logging
from typing import List

from noteforge.context import PipelineContext, StageErrorKind, StageError

logger = logging.getLogger('noteforge.engine.pipeline')


class Pipeline:
    """流水线编排器 — 按顺序执行各阶段"""

    def __init__(self, stages=None):
        """
        Args:
            stages: PipelineStage 列表（按执行顺序）
        """
        self._stages = stages or []
        self._validate_order()

    def _validate_order(self) -> None:
        """校验 stage 顺序：每个 stage 的 requires 必须在它之前出现。"""
        seen: set = set()
        for stage in self._stages:
            requires = getattr(stage, 'requires', None)
            if not isinstance(requires, (set, frozenset)):
                # 未声明 requires → 视为无依赖，但仍加入 seen
                seen.add(stage.name)
                continue
            missing = requires - seen
            if missing:
                raise ValueError(
                    f"Stage '{stage.name}' requires {missing} "
                    f"but they appear later in the pipeline"
                )
            seen.add(stage.name)

    def add_stage(self, stage) -> None:
        """添加阶段（自动校验顺序）"""
        self._stages.append(stage)
        self._validate_order()

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
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                ctx.error = StageError(
                    stage=stage_name,
                    message=str(e),
                    kind=StageErrorKind.FATAL,
                )
                logger.error(f"[{stage_name}] 执行失败: {e}", exc_info=True)
                return ctx  # 返回含 note_text 的上下文，不丢弃已生成内容
            if ctx.error:
                logger.warning(f"[{stage_name}] 阶段报告错误: {ctx.error}")
                return ctx  # 同上：允许调用方从 ctx.note_text 恢复
            logger.info(f"[{stage_name}] 完成")
        return ctx

    @property
    def stage_names(self) -> List[str]:
        """所有阶段名称"""
        return [s.name for s in self._stages]
