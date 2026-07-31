# -*- coding: utf-8 -*-
"""
NoteForge Generate Stage — 质量反馈循环 + 分块生成

从 note_engine.py 提取的核心生成逻辑：
- _generate_with_quality_loop
- _generate_chunked
- _generate_chunk_summary
- _merge_chunk_notes

作为 Pipeline 的一个阶段，接收 PipelineContext，
执行 LLM 生成 + 质量反馈循环，返回更新后的 PipelineContext。
"""

import logging
from typing import List, Optional, Callable

from pathlib import Path

from noteforge.core.llm_providers import LLMProvider, LLMError
from noteforge.core.prompt_builder import PromptBuilder
from noteforge.quality.manager import QualityManager
from noteforge.context import PipelineContext
from noteforge.engine.stages.config import GenerationConfig
from noteforge.engine.stages.base import PipelineStage


class GenerateStage(PipelineStage):
    """
    LLM 生成阶段 — 含质量反馈循环和分块生成

    从 PipelineContext 读取:
      - clean_text, chunks, title, mode, content_type
      - context_prefix (如果 with_context=True)

    写入 PipelineContext:
      - note_text (生成的笔记文本)
      - attempts (实际重试次数)
    """

    def __init__(self,
                 prompt_builder: PromptBuilder,
                 quality_manager: QualityManager,
                 provider: LLMProvider,
                 config: Optional[GenerationConfig] = None,
                 track_tokens_fn: Optional[Callable] = None):
        """
        Args:
            prompt_builder: PromptBuilder 实例
            quality_manager: QualityManager 实例
            provider: LLM 提供商
            config: 生成配置（质量/重试/温度等）
            track_tokens_fn: token 追踪回调 (provider, purpose) -> None
        """
        self.prompt_builder = prompt_builder
        self.quality_manager = quality_manager
        self.provider = provider
        self.config = config or GenerationConfig()
        self.track_tokens_fn = track_tokens_fn
        self.logger = logging.getLogger('noteforge.engine.stages.generate')

    @property
    def name(self) -> str:
        return "generate"

    requires = {"preprocess"}

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        """执行生成阶段"""
        note_text, attempts = self._generate_with_quality_loop(
            transcript=ctx.clean_text,
            chunks=ctx.chunks,
            title=ctx.title,
            context_prefix=ctx.context_prefix,
            mode=ctx.mode,
        )

        ctx.attempts = attempts
        if note_text is None:
            ctx.error = "生成失败（已耗尽重试）"
        else:
            ctx.note_text = note_text

        return ctx

    def _generate_with_quality_loop(
        self,
        transcript: str,
        chunks: List[str],
        title: str,
        context_prefix: str = "",
        mode: str = "notes",
    ) -> tuple:
        """带质量反馈的生成循环"""
        cfg = self.config
        if mode == 'meeting':
            system_prompt = self.prompt_builder.build_meeting_system_prompt()
        else:
            system_prompt = self.prompt_builder.build_system_prompt()

        last_note_text = ""
        last_quality_report = None
        attempts = 0

        for attempt in range(1 + cfg.max_retries):
            attempts = attempt + 1
            temperature = cfg.base_temperature + (attempt * cfg.retry_temp_delta)

            try:
                if attempt == 0:
                    if len(chunks) == 1:
                        transcript_with_context = chunks[0]
                        if context_prefix:
                            transcript_with_context = (
                                context_prefix + "\n\n---\n\n" + chunks[0]
                            )
                        if mode == 'meeting':
                            user_prompt = self.prompt_builder.build_meeting_user_prompt(
                                transcript_with_context, title
                            )
                        else:
                            user_prompt = self.prompt_builder.build_user_prompt(
                                transcript_with_context, title
                            )
                    else:
                        result, chunk_attempts = self._generate_chunked(
                            chunks, title,
                            system_prompt, cfg.base_temperature,
                            context_prefix=context_prefix, mode=mode
                        )
                        return (result, chunk_attempts)

                    self.logger.info(
                        f"调用 LLM (attempt {attempt + 1}, "
                        f"temp={temperature:.1f})..."
                    )
                    note_text = self.provider.generate(
                        system_prompt, user_prompt,
                        temperature=temperature
                    )
                    if self.track_tokens_fn:
                        self.track_tokens_fn(self.provider, "generate")
                else:
                    self.logger.info(
                        f"质量未达标，重试 {attempt}/{cfg.max_retries} "
                        f"(temp={temperature:.1f})..."
                    )
                    if last_quality_report and last_note_text:
                        feedback_prompt = self.prompt_builder.build_feedback_prompt(
                            transcript, last_note_text,
                            last_quality_report
                        )
                    else:
                        self.logger.info("首次调用失败，使用原始 prompt 重试")
                        retry_transcript = transcript
                        if context_prefix and len(chunks) == 1:
                            retry_transcript = (
                                context_prefix + "\n\n---\n\n" + chunks[0]
                            )
                        if mode == 'meeting':
                            feedback_prompt = self.prompt_builder.build_meeting_user_prompt(
                                retry_transcript, title
                            )
                        else:
                            feedback_prompt = self.prompt_builder.build_user_prompt(
                                retry_transcript, title, mode=mode
                            )
                    note_text = self.provider.generate(
                        system_prompt, feedback_prompt,
                        temperature=temperature
                    )
                    if self.track_tokens_fn:
                        self.track_tokens_fn(self.provider, "retry")

                last_note_text = note_text
                if cfg.save_intermediate:
                    self.quality_manager.save_intermediate(
                        title, attempt, note_text, Path(cfg.logs_dir)
                    )

                report = self.quality_manager.run_quality_gate_on_text(
                    note_text, transcript
                )
                last_quality_report = report

                if report and report.get('overall_passed', False):
                    self.logger.info(
                        f"质量通过 (score={report['total_score']:.0%}, "
                        f"attempt={attempt + 1})"
                    )
                    return (note_text, attempts)
                elif report:
                    self.logger.warning(
                        f"质量未达标: score={report['total_score']:.0%}, "
                        f"issues={sum(len(r.get('issues', [])) for r in report.get('rule_results', {}).values())}"
                    )
                    if attempt == cfg.max_retries:
                        self.logger.warning("已达最大重试次数，使用当前版本")
                        return (note_text, attempts)
                else:
                    return (note_text, attempts)

            except LLMError as e:
                if not e.retryable:
                    raise
                self.logger.error(f"LLM 调用失败 (attempt {attempt + 1}): {e}")
                if attempt == cfg.max_retries:
                    return (None, attempts)

        return (None, attempts)

    def _generate_chunked(
        self,
        chunks: List[str],
        title: str,
        system_prompt: str,
        temperature: float,
        context_prefix: str = "",
        mode: str = "notes",
    ) -> tuple:
        """分块生成并合并（渐进式摘要：每块的摘要作为下块的上下文）"""
        partial_notes: List[str] = []
        running_summary = ""

        for i, chunk in enumerate(chunks):
            self.logger.info(
                f"处理块 {i + 1}/{len(chunks)} "
                f"({len(chunk)} chars)..."
            )

            chunk_parts: List[str] = []

            if i == 0 and context_prefix:
                chunk_parts.append(context_prefix)

            if running_summary:
                chunk_parts.append(
                    f"## 前序内容摘要（第 1-{i} 部分的提炼）\n\n"
                    f"{running_summary}\n\n"
                    f"---\n\n"
                    f"请注意：以上是前面部分的摘要，请保持与前文的连贯性，"
                    f"不要重复已覆盖的内容，继续提炼以下新内容。"
                )

            chunk_parts.append(chunk)
            chunk_with_context = "\n\n".join(chunk_parts)

            chunk_title = f"{title} (第{i + 1}部分/共{len(chunks)}部分)"
            if mode == 'meeting':
                user_prompt = self.prompt_builder.build_meeting_user_prompt(
                    chunk_with_context, chunk_title
                )
            else:
                user_prompt = self.prompt_builder.build_user_prompt(
                    chunk_with_context, chunk_title
                )

            try:
                partial = self.provider.generate(
                    system_prompt, user_prompt,
                    temperature=temperature
                )
                if self.track_tokens_fn:
                    self.track_tokens_fn(self.provider, "chunk")
                partial_notes.append(partial)

                if i < len(chunks) - 1:
                    running_summary = self._generate_chunk_summary(
                        system_prompt, partial, running_summary,
                        temperature
                    )
                    self.logger.info(
                        f"块 {i + 1} 摘要: {len(running_summary)} 字"
                    )

            except LLMError as e:
                self.logger.error(f"块 {i + 1} 生成失败: {e}")
                if not e.retryable:
                    raise
                self.logger.warning(
                    f"块 {i + 1}/{len(chunks)} 因可重试错误跳过，"
                    f"最终笔记可能不完整"
                )

        if not partial_notes:
            return (None, 1)

        if len(partial_notes) == 1:
            return (partial_notes[0], 1)

        return (self._merge_chunk_notes(partial_notes, title), 1)

    def _generate_chunk_summary(
        self,
        system_prompt: str,
        chunk_note: str,
        prev_summary: str,
        temperature: float
    ) -> str:
        """为单块笔记生成摘要，供下一块作为上下文"""
        summary_prompt = (
            "请对以下笔记内容进行精炼摘要（300-500字），提取：\n"
            "1. 已覆盖的核心主题和框架\n"
            "2. 已提取的关键洞察\n"
            "3. 待续话题（本块末尾未完成的内容）\n\n"
        )

        if prev_summary:
            summary_prompt += f"## 前序摘要\n{prev_summary}\n\n"
        summary_prompt += f"## 本块笔记\n{chunk_note}"

        try:
            summary = self.provider.generate(
                system_prompt,
                summary_prompt,
                max_tokens=1024,
                temperature=max(0.1, temperature - 0.1)
            )
            if self.track_tokens_fn:
                self.track_tokens_fn(self.provider, "summary")
            return summary
        except LLMError:
            return prev_summary

    @staticmethod
    def _merge_chunk_notes(notes: List[str], title: str) -> str:
        """合并分块生成的笔记"""
        result_parts: List[str] = []

        for i, note in enumerate(notes):
            if i == 0:
                result_parts.append(note)
            else:
                lines = note.split('\n')
                content_start = 0
                for j, line in enumerate(lines):
                    if line.startswith('## ') and '核心观点' in line:
                        content_start = j
                        break
                    elif line.startswith('## '):
                        content_start = j
                        break
                if content_start > 0:
                    result_parts.append('\n'.join(lines[content_start:]))
                else:
                    result_parts.append(note)

        return '\n\n'.join(result_parts)
