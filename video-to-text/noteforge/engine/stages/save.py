# -*- coding: utf-8 -*-
"""
NoteForge Save Stage — 笔记保存 + 中文名副本

从 note_engine.py generate_note() Step 7 提取：
- 保存笔记（write_file）
- 创建中文名副本（如果标题含中文但输出路径为 ASCII）

从 PipelineContext 读取：formatted_text, output_path, title
副作用：写文件
"""

import os
import shutil
import logging
from pathlib import Path
from typing import Optional

from noteforge.context import PipelineContext
from noteforge.engine.stages.base import PipelineStage
from noteforge.infra.file_io import write_file


class SaveStage(PipelineStage):
    """
    保存阶段 — 写入笔记文件 + 中文名副本

    从 PipelineContext 读取:
      - formatted_text: 格式化后的笔记文本
      - output_path: 输出路径
      - title: 笔记标题

    副作用:
      - 写入笔记文件
      - 可选创建中文名副本
    """

    def __init__(self,
                 notes_dir: Path,
                 logger: Optional[logging.Logger] = None):
        """
        Args:
            notes_dir: 笔记输出目录（Path）
            logger: 日志记录器
        """
        self.notes_dir = notes_dir
        self.logger = logger or logging.getLogger('noteforge.engine.stages.save')

    @property
    def name(self) -> str:
        return "save"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        """执行保存阶段

        设计：位于 QualityGateStage 之后，仅在实际通过质量门禁时保存，
        避免未通过的低质量笔记残留为孤立文件。
        若质量门禁未运行（quality_report 为 None），仍保存笔记。
        """
        # 质量门禁未通过 → 不保存孤立文件，仅记录警告
        if ctx.quality_report is not None and not ctx.overall_passed:
            self.logger.warning(
                f"笔记未通过质量门禁 (score={ctx.total_score:.2f})，跳过保存: "
                f"{ctx.output_path}"
            )
            return ctx

        # Step 7: 保存笔记
        write_file(ctx.output_path, ctx.formatted_text)
        self.logger.info(f"笔记已保存: {ctx.output_path}")

        # 自动创建中文名副本（当标题含中文但输出路径为 ASCII 时）
        # 解决命令行中文引号等特殊字符路径问题
        output_stem = Path(ctx.output_path).stem
        if ctx.title and ctx.title != output_stem:
            if (any(ord(c) > 127 for c in ctx.title)
                    and not any(ord(c) > 127 for c in output_stem)):
                chinese_path = str(
                    Path(ctx.output_path).parent / f"{ctx.title}.md"
                )
                if not os.path.exists(chinese_path):
                    shutil.copy2(ctx.output_path, chinese_path)
                    self.logger.info(f"中文名副本: {chinese_path}")

        return ctx
