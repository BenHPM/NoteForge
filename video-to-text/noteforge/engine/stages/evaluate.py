# -*- coding: utf-8 -*-
"""
NoteForge Quality Gate Stage — 最终质量评估 + 报告保存

从 note_engine.py generate_note() Step 8 提取：
- 运行质量门禁（QualityManager.run_quality_gate）
- 保存质量报告（QualityManager.save_quality_report）

从 PipelineContext 读取：output_path, transcript_path
写入 PipelineContext：quality_report, total_score, overall_passed
"""

import logging
from typing import Optional

from noteforge.context import PipelineContext
from noteforge.engine.stages.base import PipelineStage
from noteforge.quality.manager import QualityManager


class QualityGateStage(PipelineStage):
    """
    质量门禁阶段 — 最终质量评估 + 报告保存

    从 PipelineContext 读取:
      - output_path: 笔记输出路径
      - transcript_path: 转写文件路径

    写入 PipelineContext:
      - quality_report: 质量报告字典
      - total_score: 综合评分
      - overall_passed: 是否通过
    """

    def __init__(self,
                 quality_manager: QualityManager,
                 reports_dir,
                 logger: Optional[logging.Logger] = None):
        """
        Args:
            quality_manager: QualityManager 实例
            reports_dir: 质量报告输出目录
            logger: 日志记录器
        """
        self.quality_manager = quality_manager
        self.reports_dir = reports_dir
        self.logger = logger or logging.getLogger('noteforge.engine.stages.quality_gate')

    @property
    def name(self) -> str:
        return "quality_gate"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        """执行质量门禁阶段"""
        # Step 8: 最终质量评估
        final_report = self.quality_manager.run_quality_gate(
            ctx.output_path, ctx.transcript_path
        )
        if final_report:
            ctx.quality_report = final_report
            ctx.total_score = final_report.get('total_score', 0)
            ctx.overall_passed = final_report.get('overall_passed', False)
            self.quality_manager.save_quality_report(
                ctx.output_path, final_report
            )

        return ctx
