# -*- coding: utf-8 -*-
"""
NoteForge Post-Process Stage — Token 记录 + 飞书同步 + 自动合成触发

从 note_engine.py generate_note() Step 9-11 提取：
- Token 使用量记录（_track_tokens → provider.get_total_usage）
- 飞书同步（_try_feishu_sync）
- 自动合成触发（_auto_trigger_synthesis）

从 PipelineContext 读取：output_path, formatted_text
写入 PipelineContext：token_usage

注意：飞书同步和自动合成是副作用操作，失败不阻断流程。
"""

import logging
from typing import Optional, Callable

from noteforge.context import PipelineContext
from noteforge.engine.stages.base import PipelineStage


class PostProcessStage(PipelineStage):
    """
    后处理阶段 — Token 记录 + 飞书同步 + 自动合成触发

    从 PipelineContext 读取:
      - output_path: 笔记输出路径
      - formatted_text: 格式化后的笔记文本

    写入 PipelineContext:
      - token_usage: Token 使用量字典

    副作用:
      - 飞书同步（失败不阻断）
      - 自动合成触发（失败不阻断）
    """

    def __init__(self,
                 get_total_usage_fn: Optional[Callable] = None,
                 try_feishu_sync_fn: Optional[Callable] = None,
                 auto_trigger_synthesis_fn: Optional[Callable] = None,
                 logger: Optional[logging.Logger] = None):
        """
        Args:
            get_total_usage_fn: 获取 provider 总 token 使用量的回调，
                签名 () -> dict，返回 {'input_tokens': int, 'output_tokens': int, 'calls': int}
            try_feishu_sync_fn: 飞书同步回调，
                签名 (output_path: str, note_text: str) -> None
            auto_trigger_synthesis_fn: 自动合成触发回调，
                签名 (note_path: str) -> None
            logger: 日志记录器
        """
        self.get_total_usage_fn = get_total_usage_fn
        self.try_feishu_sync_fn = try_feishu_sync_fn
        self.auto_trigger_synthesis_fn = auto_trigger_synthesis_fn
        self.logger = logger or logging.getLogger('noteforge.engine.stages.postprocess')

    @property
    def name(self) -> str:
        return "postprocess"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        """执行后处理阶段"""
        # Step 9: 记录 token 使用量
        if self.get_total_usage_fn:
            usage = self.get_total_usage_fn()
            if usage:
                ctx.token_usage = usage
                self.logger.info(
                    f"Token 消耗: input={usage['input_tokens']:,} "
                    f"output={usage['output_tokens']:,} "
                    f"calls={usage['calls']}"
                )

        # Step 10: 飞书知识库同步（可选，失败不阻断）
        if self.try_feishu_sync_fn:
            try:
                self.try_feishu_sync_fn(ctx.output_path, ctx.formatted_text)
            except Exception as e:
                self.logger.warning(f"飞书同步异常（不影响笔记生成）: {e}")

        # Step 11: 自动触发跨集知识合成（同域笔记新增时）
        # 批量模式下跳过单篇自动合成，由 generate_batch 统一触发
        if self.auto_trigger_synthesis_fn and not ctx.batch_mode:
            try:
                self.auto_trigger_synthesis_fn(ctx.output_path)
            except Exception as e:
                self.logger.warning(f"自动合成异常（不影响笔记生成）: {e}")

        return ctx
