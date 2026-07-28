# -*- coding: utf-8 -*-
"""
NoteForge Preprocess Stage — 转写文本读取 + 清洗 + 分块

从 note_engine.py generate_note() Step 1-2 提取：
- 读取转写文件
- 文本清洗（TranscriptPreprocessor.clean）
- 统计信息（get_transcript_stats）
- 短文本检查（<100 字 → R0 基线不通过）
- 长文本分块（chunk_if_needed）
- 关联笔记上下文注入

从 PipelineContext 读取：transcript_path, with_context, context_limit
写入 PipelineContext：raw_text, clean_text, chunks, context_prefix, warnings
"""

import logging
from typing import Optional

from noteforge.context import PipelineContext, StageError, StageErrorKind
from noteforge.engine.stages.base import PipelineStage
from noteforge.core.transcript_preprocessor import TranscriptPreprocessor
from noteforge.infra.file_io import read_file


class PreprocessStage(PipelineStage):
    """
    预处理阶段 — 读取转写文本、清洗、分块、上下文注入

    从 PipelineContext 读取:
      - transcript_path: 转写文件路径
      - with_context: 是否注入关联笔记上下文
      - context_limit: 上下文笔记数量上限

    写入 PipelineContext:
      - raw_text: 原始转写文本
      - clean_text: 清洗后文本
      - chunks: 分块结果
      - context_prefix: 关联笔记上下文前缀
      - warnings: 追加警告
    """

    def __init__(self,
                 preprocessor: TranscriptPreprocessor,
                 transcript_config: dict,
                 get_related_context_fn=None,
                 logger: Optional[logging.Logger] = None):
        """
        Args:
            preprocessor: TranscriptPreprocessor 实例
            transcript_config: 转写配置（来自 config['transcript']）
            get_related_context_fn: 获取关联笔记上下文的回调，
                签名 (content: str, limit: int) -> str
            logger: 日志记录器
        """
        self.preprocessor = preprocessor
        self.transcript_config = transcript_config
        self.get_related_context_fn = get_related_context_fn
        self.logger = logger or logging.getLogger('noteforge.engine.stages.preprocess')

    @property
    def name(self) -> str:
        return "preprocess"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        """执行预处理阶段"""
        # Step 1: 读取并预处理转写文本
        self.logger.debug(f"读取转写文件: {ctx.transcript_path}")
        raw_text = read_file(ctx.transcript_path)
        ctx.raw_text = raw_text

        clean_text = self.preprocessor.clean(
            raw_text,
            clean_fillers=self.transcript_config.get('clean_fillers', True),
            clean_unrecognized=self.transcript_config.get('clean_unrecognized', True),
            clean_timestamps=self.transcript_config.get('clean_timestamps', True),
        )
        ctx.clean_text = clean_text

        stats = self.preprocessor.get_transcript_stats(clean_text)
        self.logger.debug(
            f"转写文本: {stats['char_count']} 字, "
            f"~{stats['estimated_tokens']} tokens"
        )

        # R0 基线：短文本检查
        if stats['char_count'] < 100:
            self.logger.warning("转写文本过短，跳过生成")
            ctx.error = "转写文本过短"
            return ctx
        elif stats['char_count'] < 200:
            ctx.warnings.append(
                f"转写文本较短（{stats['char_count']} 字），R0 基线风险"
            )

        # Step 2: 处理长文本分块
        max_tokens = self.transcript_config.get('max_tokens_per_call', 50000)
        overlap_tokens = self.transcript_config.get('chunk_overlap_tokens', 1000)
        min_chunk = self.transcript_config.get('min_chunk_size_tokens', 5000)
        chunks = self.preprocessor.chunk_if_needed(
            clean_text, max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            min_chunk_size=min_chunk
        )
        ctx.chunks = chunks
        if len(chunks) > 1:
            ctx.warnings.append(f"转写文本较长，已分为 {len(chunks)} 个块")
        self.logger.info(f"分为 {len(chunks)} 个块处理")

        # Step 2.5: 关联笔记上下文注入
        if ctx.with_context and self.get_related_context_fn:
            context_prefix = self.get_related_context_fn(
                clean_text, limit=ctx.context_limit
            )
            if context_prefix:
                self.logger.info(
                    f"已注入相关笔记上下文 ({len(context_prefix)} 字)"
                )
            ctx.context_prefix = context_prefix

        return ctx
