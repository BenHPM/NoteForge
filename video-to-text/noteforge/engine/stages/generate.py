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
import re as _re
from typing import List, Optional, Callable

from pathlib import Path

from noteforge.core.llm_providers import LLMProvider, LLMError
from noteforge.core.prompt_builder import PromptBuilder
from noteforge.quality.manager import QualityManager
from noteforge.context import PipelineContext
from noteforge.engine.stages.config import GenerationConfig, FACTUAL_CONTENT_TYPES
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

    required_inputs = frozenset({"clean_text", "chunks", "title", "content_type"})
    provided_outputs = frozenset({"note_text", "attempts"})

    # Risk-4: 重试成本爆炸防护
    # 单次生成的 token 预算上限（input + output 累计）
    # 180K context × 3 retries × N chunks 无上限时，一次生成可能消耗 $5+
    # 设置预算上限后，超限立即停止重试，使用当前最佳版本
    DEFAULT_TOKEN_BUDGET = 500000  # 500K tokens ≈ $1.5 (Claude Sonnet)

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

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        """执行生成阶段"""
        note_text, attempts = self._generate_with_quality_loop(
            transcript=ctx.clean_text,
            chunks=ctx.chunks,
            title=ctx.title,
            context_prefix=ctx.context_prefix,
            mode=ctx.mode,
            content_type=ctx.content_type,
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
        content_type: str = "",
    ) -> tuple:
        """带质量反馈的生成循环"""
        cfg = self.config
        if mode == 'meeting':
            system_prompt = self.prompt_builder.build_meeting_system_prompt()
        else:
            system_prompt = self.prompt_builder.build_system_prompt()

        # P0/P1-2: 事实性内容类型冻结重试温度（策略可配置）
        is_factual = cfg.should_freeze_temperature(content_type)

        last_note_text = ""
        last_quality_report = None
        attempts = 0

        # Risk-4: Token 预算追踪
        tokens_spent = 0
        token_budget = getattr(cfg, 'token_budget', self.DEFAULT_TOKEN_BUDGET)

        for attempt in range(1 + cfg.max_retries):
            attempts = attempt + 1
            # P0: 事实性任务冻结温度，创意任务允许递增
            if is_factual:
                temperature = cfg.base_temperature
            else:
                temperature = cfg.base_temperature + (attempt * cfg.retry_temp_delta)

            # Risk-4: 检查 token 预算
            if tokens_spent >= token_budget:
                self.logger.warning(
                    f"Token 预算耗尽 ({tokens_spent}/{token_budget})，"
                    f"停止重试，使用当前最佳版本"
                )
                if last_note_text:
                    return (last_note_text, attempts)
                return (None, attempts)

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
                            context_prefix=context_prefix, mode=mode,
                            content_type=content_type,
                            token_budget=token_budget - tokens_spent,
                        )
                        return (result, chunk_attempts)

                    self.logger.info(
                        f"调用 LLM (attempt {attempt + 1}, "
                        f"temp={temperature:.1f})..."
                    )
                    note_text = self.provider.generate(
                        system_prompt, user_prompt,
                        max_tokens=self.config.max_tokens,
                        temperature=temperature
                    )
                    if self.track_tokens_fn:
                        self.track_tokens_fn(self.provider, "generate")
                    # Risk-4: 累计 token 消耗
                    usage = self.provider.get_usage()
                    tokens_spent += usage.get('input_tokens', 0) + usage.get('output_tokens', 0)
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
                        max_tokens=self.config.max_tokens,
                        temperature=temperature
                    )
                    if self.track_tokens_fn:
                        self.track_tokens_fn(self.provider, "retry")
                    # Risk-4: 累计 token 消耗
                    usage = self.provider.get_usage()
                    tokens_spent += usage.get('input_tokens', 0) + usage.get('output_tokens', 0)

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
        content_type: str = "",
        token_budget: int = 0,
    ) -> tuple:
        """分块生成并合并（渐进式摘要：每块的摘要作为下块的上下文）

        Risk-4: token_budget 参数控制分块生成的总 token 消耗上限。
        当累计消耗超过预算时，跳过剩余块，使用已生成的部分。
        """
        partial_notes: List[str] = []
        running_summary = ""
        ct = content_type or ('meeting' if mode == 'meeting' else 'lecture')
        # Risk-4: 分块 token 预算追踪
        chunk_tokens_spent = 0
        effective_budget = token_budget if token_budget > 0 else self.DEFAULT_TOKEN_BUDGET

        for i, chunk in enumerate(chunks):
            self.logger.info(
                f"处理块 {i + 1}/{len(chunks)} "
                f"({len(chunk)} chars)..."
            )

            # Risk-4: 检查分块 token 预算
            if chunk_tokens_spent >= effective_budget:
                self.logger.warning(
                    f"分块 token 预算耗尽 ({chunk_tokens_spent}/{effective_budget})，"
                    f"跳过剩余 {len(chunks) - i} 块"
                )
                break

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
                    max_tokens=self.config.max_tokens,
                    temperature=temperature
                )
                if self.track_tokens_fn:
                    self.track_tokens_fn(self.provider, "chunk")
                # Risk-4: 累计分块 token 消耗
                usage = self.provider.get_usage()
                chunk_tokens_spent += usage.get('input_tokens', 0) + usage.get('output_tokens', 0)

                # P0: 轻量级分块结构验证
                validation_issues = self._validate_chunk_structure(partial, ct)
                if validation_issues:
                    self.logger.warning(
                        f"块 {i + 1} 结构验证失败: {'; '.join(validation_issues)}"
                    )
                    # 结构验证失败 → 重试一次（冻结温度）
                    # P0 修复: 去掉"不是最后一块才重试"条件
                    # 末块截断/结构失败同样需要重试，否则污染会直接混入最终笔记
                    # （2026-08-09 实测：末块撞 max_tokens=8192 被截断，LLM 元推理泄漏进笔记尾部）
                    truncated = self._was_truncated()
                    retry_temp = self.config.base_temperature  # 冻结温度
                    retry_max_tokens = self.config.max_tokens
                    if truncated:
                        # 截断 → 提高 max_tokens 给足空间，避免二次截断
                        retry_max_tokens = max(
                            self.config.max_tokens,
                            self.config.truncation_retry_max_tokens,
                        )
                        self.logger.info(
                            f"块 {i + 1} 疑似截断 (stop_reason=max_tokens)，"
                            f"用 max_tokens={retry_max_tokens} 重试..."
                        )
                    else:
                        self.logger.info(
                            f"块 {i + 1} 结构重试 (temp={retry_temp:.1f})..."
                        )
                    try:
                        partial = self.provider.generate(
                            system_prompt, user_prompt,
                            max_tokens=retry_max_tokens,
                            temperature=retry_temp
                        )
                        if self.track_tokens_fn:
                            self.track_tokens_fn(self.provider, "chunk_retry")
                        retry_issues = self._validate_chunk_structure(partial, ct)
                        if retry_issues:
                            self.logger.warning(
                                f"块 {i + 1} 重试后仍异常: {'; '.join(retry_issues)}"
                            )
                    except LLMError:
                        pass  # 重试失败，使用原始输出

                partial_notes.append(partial)

                # P1-1: 跨块实体连续性检查
                # 检测相邻块之间的人名/术语漂移
                if i > 0 and partial_notes:
                    drift_issues = self._check_entity_drift(
                        partial_notes[-2] if len(partial_notes) >= 2 else partial_notes[-1],
                        partial,
                    )
                    if drift_issues:
                        self.logger.warning(
                            f"块 {i}/{len(chunks)} 实体漂移: {'; '.join(drift_issues)}"
                        )

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
    def _validate_chunk_structure(chunk_text: str,
                                   content_type: str = "") -> List[str]:
        """P0: 轻量级分块结构验证（零 API 成本，捕获 80% 分块级问题）

        检查项：
        1. 必需 section 标记存在（按 content_type）
        2. 无中句截断痕迹
        3. 非空且非拒绝文本
        4. 有实质内容（非纯标题列表）

        Returns:
            问题列表（空 = 通过）
        """
        issues = []

        if not chunk_text or not chunk_text.strip():
            issues.append("空内容")
            return issues

        stripped = chunk_text.strip()

        # 检查拒绝文本
        refusal_patterns = [
            r'I\s+cannot\s+(?:complete|fulfill|generate)',
            r"I'm\s+unable\s+to",
            r'as\s+an\s+AI',
            r'内容\s*(?:违反|违规|敏感)',
        ]
        for pat in refusal_patterns:
            if _re.search(pat, stripped, _re.IGNORECASE):
                issues.append(f"疑似LLM拒绝文本")
                break

        # 检查中句截断（行末无标点、无标题标记、无列表标记）
        lines = stripped.split('\n')
        if lines:
            last_content_line = ''
            for line in reversed(lines):
                if line.strip() and not line.strip().startswith('#'):
                    last_content_line = line.strip()
                    break
            if last_content_line:
                # 非标题/列表/引用行，末尾应有标点
                has_punctuation = bool(_re.search(
                    r'[。！？：；、）》」\-\-]$', last_content_line
                ))
                is_list_item = last_content_line.startswith(('- ', '* ', '1.', '2.'))
                is_table_row = last_content_line.startswith('|')
                if not has_punctuation and not is_list_item and not is_table_row:
                    issues.append(f"疑似截断: 末行 '{last_content_line[-50:]}' 无标点结尾")

        # 检查有实质内容（不是纯标题列表）
        content_lines = [
            l for l in lines
            if l.strip()
            and not l.strip().startswith('#')
            and not l.strip().startswith('---')
            and len(l.strip()) > 10
        ]
        if not content_lines:
            issues.append("无实质内容（仅标题/分隔线）")

        return issues

    def _was_truncated(self) -> bool:
        """P0: 检测上一次 LLM 输出是否被 max_tokens 截断（provider-native 信号）

        比 post-hoc 标点启发更可靠：Claude 的 stop_reason == "max_tokens"
        表示输出触顶截断。OpenAI 对应 finish_reason == "length"。
        """
        stop_reason = getattr(self.provider, '_last_stop_reason', '')
        return stop_reason in ('max_tokens', 'length')

    @staticmethod
    def _check_entity_drift(prev_chunk: str, curr_chunk: str) -> List[str]:
        """P1-1: 跨块实体连续性检查（零 API 成本）

        检测相邻块之间的实体漂移：
        1. 前块出现的人名在后块中消失（可能遗漏）
        2. 前块引入的术语在后块中未延续（可能不一致）
        3. 同一实体在不同块中名称不同（可能不一致）

        Args:
            prev_chunk: 前一块笔记
            curr_chunk: 当前块笔记

        Returns:
            漂移问题列表（空 = 无问题）
        """
        issues = []

        # 提取人名模式：中文 2-3 字 + 直接跟说话动词（无中间词）
        # 使用 (?:^|[，。、\n\s]) 确保人名前有分隔符，避免匹配到句子中间
        person_pattern = _re.compile(
            r'(?:^|[，。、\n\s「""])([一-鿿]{2,3})(?:说|认为|指出|表示|强调|提到|分析|解释|补充|回应|建议|主张|称|透露)'
        )
        _non_person_words = {
            '原文', '笔记', '总结', '分析', '框架', '方法', '观点', '理论',
            '模型', '策略', '讲师', '核心', '关键', '重要', '因此', '所以',
            '但是', '然而', '大家', '我们', '他们', '自己', '这个', '那个',
        }

        prev_names = set(
            m.group(1) for m in person_pattern.finditer(prev_chunk)
            if m.group(1) not in _non_person_words
        )
        curr_names = set(
            m.group(1) for m in person_pattern.finditer(curr_chunk)
            if m.group(1) not in _non_person_words
        )

        # 检测：前块人名在后块完全消失（可能遗漏）
        if prev_names and curr_names:
            disappeared = prev_names - curr_names
            # 只在两个块都有人名时检查（避免单块无人的误报）
            if disappeared and len(disappeared) < len(prev_names):
                # 部分人名消失是正常的（不是所有人在每块都发言）
                # 但如果消失的人名超过 2 个，可能是遗漏
                if len(disappeared) > 2:
                    issues.append(
                        f"前块人名在后块消失: {', '.join(list(disappeared)[:3])}"
                    )

        # 检测：术语/概念连续性（前块标题中的术语应在后块中延续）
        prev_terms = set(
            line.strip().lstrip('#').strip()
            for line in prev_chunk.split('\n')
            if line.strip().startswith('##') and len(line.strip()) > 5
        )
        # 去掉通用标题
        generic_terms = {'核心观点', '学习总结', '知识框架', '可迁移洞察', '行动清单'}
        prev_terms -= generic_terms

        if prev_terms:
            # 检查前块核心术语是否在后块中至少出现一次
            missing_terms = []
            for term in prev_terms:
                # 取术语中的关键词（2-4 字）
                keywords = _re.findall(r'[一-鿿]{2,4}', term)
                if keywords and not any(kw in curr_chunk for kw in keywords):
                    missing_terms.append(term[:20])
            if len(missing_terms) > 2:
                # 多个术语消失可能正常（后块是不同主题），但超过阈值时警告
                issues.append(
                    f"前块 {len(missing_terms)} 个术语在后块未延续"
                )

        return issues

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
