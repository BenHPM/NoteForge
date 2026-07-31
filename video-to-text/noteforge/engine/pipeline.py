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
        """校验 stage 顺序合法性。

        两层校验：
        1. 旧式 requires（按 stage name）：每个 stage 的 requires 必须在它之前出现。
        2. 新式 required_inputs / provided_outputs（按数据字段）：
           每个 stage 的 required_inputs 必须被先前 stage 的 provided_outputs 覆盖，
           或由 PipelineContext 的输入字段（source_path, output_path, title, content_type 等）
           在构造时已提供。
        """
        # PipelineContext 构造时即存在的输入字段（阶段 0 设置，无需 prior stage 提供）
        ctx_input_fields = frozenset({
            'source_path', 'output_path', 'title', 'content_type',
            'mode', 'force', 'with_context', 'context_limit',
            'context_prefix', 'batch_mode', 'transcript_path',
        })

        seen_names: set = set()
        available_outputs: frozenset = ctx_input_fields

        for stage in self._stages:
            # --- 旧式 requires 校验（向后兼容）---
            requires = getattr(stage, 'requires', None)
            if isinstance(requires, (set, frozenset)):
                missing = requires - seen_names
                if missing:
                    raise ValueError(
                        f"Stage '{stage.name}' requires {missing} "
                        f"but they appear later in the pipeline"
                    )

            # --- 新式 required_inputs / provided_outputs 校验 ---
            required_inputs = getattr(stage, 'required_inputs', frozenset())
            if not isinstance(required_inputs, (set, frozenset)):
                required_inputs = frozenset()
            unsatisfied = required_inputs - available_outputs
            if unsatisfied:
                raise ValueError(
                    f"Stage '{stage.name}' has unsatisfied input dependencies: "
                    f"{', '.join(sorted(unsatisfied))}. "
                    f"Available from prior stages or ctx inputs: "
                    f"{', '.join(sorted(available_outputs))}"
                )

            provided_outputs = getattr(stage, 'provided_outputs', frozenset())
            if not isinstance(provided_outputs, (set, frozenset)):
                provided_outputs = frozenset()
            available_outputs = available_outputs | provided_outputs
            seen_names.add(stage.name)

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
