"""
NoteForge LLM 笔记生成引擎 v1.0
主控模块：串联预处理→prompt→LLM→格式化→质量门禁→重试循环

用法:
    python llm_note_engine.py --input ep01
    python llm_note_engine.py --batch --skip-existing
    python llm_note_engine.py --input ep01 --force
    python llm_note_engine.py --check-only note.md
"""

import os
import sys
import re
import json
import time
import logging
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict

# 修复 Windows 控制台编码问题（subprocess 调用时 emoji 等 Unicode 字符）
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 添加 scripts 目录到 path 以便 import 同级模块
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import env_check  # noqa: F401 — 检测 Python 环境（必须在其他 import 之前）

from transcript_preprocessor import TranscriptPreprocessor
from prompt_builder import PromptBuilder
from note_formatter import NoteFormatter
from llm_providers import create_provider, LLMProvider, LLMError
from token_manager import TokenManager, TokenUsage
from models import GenerationResult
from domain_classifier import DomainClassifier
from synthesis_engine import SynthesisEngine
from audio_handler import AudioHandler
from quality_manager import QualityManager
from batch_processor import BatchProcessor
from external_sync import ExternalSync



class LLMNoteEngine:
    """LLM 笔记生成引擎"""

    def __init__(self, config_path: Optional[str] = None):
        """
        Args:
            config_path: 配置文件路径。默认使用 config/llm_engine_config.yaml
        """
        if config_path is None:
            config_path = str(BASE_DIR / "config" / "llm_engine_config.yaml")

        self.config = self._load_config(config_path)

        self.logger = logging.getLogger('noteforge.engine')
        self.preprocessor = TranscriptPreprocessor()
        self.formatter = NoteFormatter()

        # 路径配置
        paths = self.config.get('paths', {})
        self.base_dir = BASE_DIR
        self.transcripts_dir = self.base_dir / paths.get('transcripts_dir', 'output/transcripts')
        self.notes_dir = self.base_dir / paths.get('notes_dir', 'output/notes')
        self.reports_dir = self.base_dir / paths.get('reports_dir', 'output/quality_reports')
        self.logs_dir = self.base_dir / self.config.get('logging', {}).get('log_dir', 'output/logs')

        self._setup_logging()

        # 确保输出目录存在
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Token 使用追踪
        self.token_manager = TokenManager(log_dir=str(self.logs_dir))

        # 质量配置
        quality_cfg = self.config.get('quality', {})
        self.min_score = quality_cfg.get('min_score', 0.80)
        self.max_retries = quality_cfg.get('max_retries', 2)
        self.retry_temp_delta = quality_cfg.get('retry_temperature_delta', 0.1)

        # Prompt 组装器（延迟初始化，需要时才加载）
        self._prompt_builder: Optional[PromptBuilder] = None
        self._content_type: Optional[str] = None
        # 当前正在处理的集数标识（用于 token 追踪）
        self._current_episode: str = ""
        # 知识域分类器
        domains = self.config.get('knowledge_domains', [])
        self._domain_classifier = DomainClassifier(
            domains=domains, base_dir=self.base_dir, notes_dir=self.notes_dir
        )
        # 音频处理器
        self._audio_handler = AudioHandler(
            transcripts_dir=self.transcripts_dir,
            base_dir=self.base_dir,
            logger=self.logger,
        )
        # 质量管理器
        self._quality_manager = QualityManager(
            reports_dir=self.reports_dir,
            notes_dir=self.notes_dir,
            base_dir=self.base_dir,
            logger=self.logger,
            config=self.config,
        )
        # 知识合成引擎
        self._synthesis_engine = SynthesisEngine(
            domain_classifier=self._domain_classifier,
            notes_dir=self.notes_dir,
            base_dir=self.base_dir,
            logger=self.logger,
            track_tokens_fn=self._track_tokens,
        )
        # 批量处理器
        self._batch_processor = BatchProcessor(
            notes_dir=self.notes_dir,
            transcripts_dir=self.transcripts_dir,
            logger=self.logger,
            token_manager=self.token_manager,
        )
        # 外部同步处理器
        self._external_sync = ExternalSync(
            base_dir=self.base_dir,
            notes_dir=self.notes_dir,
            logger=self.logger,
        )
        # LLM 提供商（延迟初始化）
        self._provider: Optional[LLMProvider] = None
        # 质量反馈循环状态
        self._last_note_text: str = ""
        self._last_quality_report: Optional[dict] = None

    # 知识域代理属性（委托给 DomainClassifier）
    def detect_domain(self, note_path: str) -> str:
        return self._domain_classifier.detect_domain(note_path)

    def get_domain_config(self, domain_id: str) -> dict:
        return self._domain_classifier.get_domain_config(domain_id)

    def get_notes_by_domain(self, note_paths: List[str] = None) -> Dict[str, List[str]]:
        return self._domain_classifier.get_notes_by_domain(note_paths)

    def validate_domain_match(self, note_path: str, synthesis_path: str) -> tuple:
        return self._domain_classifier.validate_domain_match(note_path, synthesis_path)

    def _track_tokens(self, provider: LLMProvider, purpose: str = "generate"):
        """从 provider 读取最近一次调用的 token 使用量并记录"""
        usage = provider.get_usage()
        if usage.get('input_tokens', 0) > 0:
            # 获取缓存统计（仅 Claude provider）
            cached = 0
            if hasattr(provider, '_last_cache_read'):
                cached = provider._last_cache_read
            self.token_manager.record(TokenUsage(
                episode=self._current_episode,
                input_tokens=usage['input_tokens'],
                output_tokens=usage['output_tokens'],
                cached_tokens=cached,
                model=provider.get_name(),
                purpose=purpose,
            ))

    def _load_config(self, config_path: str) -> dict:
        """加载配置文件"""
        import yaml
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def _setup_logging(self):
        """配置日志（控制台 + 持久化文件）"""
        log_cfg = self.config.get('logging', {})
        level = getattr(logging, log_cfg.get('level', 'INFO').upper(),
                        logging.INFO)
        logging.basicConfig(
            level=level,
            format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        )
        # 持久化文件日志
        log_dir = self.base_dir / 'output' / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(
            str(log_dir / 'noteforge.log'), encoding='utf-8'
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            '%(asctime)s [%(name)s] %(levelname)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        logging.getLogger('noteforge').addHandler(fh)

    def _get_prompt_builder(self) -> PromptBuilder:
        """延迟初始化 PromptBuilder"""
        if self._prompt_builder is None:
            paths = self.config.get('paths', {})
            rules_path = str(self.base_dir / paths.get('rules', 'config/note_generation_rules.yaml'))
            exp_path = str(self.base_dir / paths.get('experience', 'config/experience_log.yaml'))
            example_path = paths.get('format_example', '')
            if example_path:
                example_path = str(self.base_dir / example_path)

            self._prompt_builder = PromptBuilder(
                rules_path, exp_path, example_path,
                content_type=self._content_type or 'lecture'
            )
        return self._prompt_builder

    def _get_provider(self, provider_override: Optional[str] = None) -> LLMProvider:
        """延迟初始化 LLM 提供商"""
        # 将 api_retry 配置注入到 provider 配置中
        provider_cfg = dict(self.config.get('provider', {}))
        provider_cfg['api_retry'] = self.config.get('api_retry', {})

        if provider_override:
            provider_cfg['type'] = provider_override
            return create_provider(provider_cfg)

        if self._provider is None:
            self._provider = create_provider(provider_cfg)
        return self._provider

    def generate_note(
        self,
        transcript_path: str,
        output_path: Optional[str] = None,
        title: Optional[str] = None,
        provider_override: Optional[str] = None,
        force: bool = False,
        with_context: bool = False,
        context_limit: int = 3,
        mode: str = 'notes'
    ) -> GenerationResult:
        """
        生成单篇笔记（主流程）

        Args:
            transcript_path: 转写文件路径
            output_path: 输出笔记路径（默认自动生成）
            title: 笔记标题（默认从文件名提取）
            provider_override: 覆盖配置中的提供商
            force: 是否覆盖已有笔记

        Returns:
            GenerationResult
        """
        start_time = time.time()
        transcript_path = str(Path(transcript_path).resolve())

        # 设置当前集数标识（用于 token 追踪）
        self._current_episode = Path(transcript_path).stem

        # 解析输出路径
        if output_path is None:
            stem = Path(transcript_path).stem
            output_path = str(self.notes_dir / f"{stem}.md")

        result = GenerationResult(transcript_path=transcript_path)

        # 检查是否已存在
        if os.path.exists(output_path) and not force:
            self.logger.info(f"笔记已存在，跳过: {output_path}")
            result.note_path = output_path
            result.error = "已存在（使用 --force 覆盖）"
            return result

        # 解析标题
        if title is None:
            title = self._audio_handler.extract_title(transcript_path)

        # 音频文件检测：如果是音频/视频文件，先转写
        audio_exts = {'.mp3', '.wav', '.m4a', '.flac', '.mp4', '.mkv', '.avi', '.mov'}
        if Path(transcript_path).suffix.lower() in audio_exts:
            transcript_path = self._audio_handler.transcribe_audio(transcript_path, result)
            if transcript_path is None:
                result.error = "音频转写失败"
                return result

        try:
            # Step 1: 读取并预处理转写文本
            self.logger.info(f"读取转写文件: {transcript_path}")
            raw_text = self._read_file(transcript_path)
            transcript_cfg = self.config.get('transcript', {})
            clean_text = self.preprocessor.clean(
                raw_text,
                clean_fillers=transcript_cfg.get('clean_fillers', True),
                clean_unrecognized=transcript_cfg.get('clean_unrecognized', True),
                clean_timestamps=transcript_cfg.get('clean_timestamps', True),
            )
            stats = self.preprocessor.get_transcript_stats(clean_text)
            self.logger.info(
                f"转写文本: {stats['char_count']} 字, "
                f"~{stats['estimated_tokens']} tokens"
            )

            if stats['char_count'] < 100:
                self.logger.warning("转写文本过短，跳过生成")
                result.error = "转写文本过短"
                return result

            # Step 2: 处理长文本分块
            max_tokens = transcript_cfg.get('max_tokens_per_call', 50000)
            overlap_tokens = transcript_cfg.get('chunk_overlap_tokens', 1000)
            min_chunk = transcript_cfg.get('min_chunk_size_tokens', 5000)
            chunks = self.preprocessor.chunk_if_needed(
                clean_text, max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
                min_chunk_size=min_chunk
            )
            self.logger.info(f"分为 {len(chunks)} 个块处理")

            # Step 2.5: 关联笔记上下文注入
            context_prefix = ""
            if with_context:
                context_prefix = self._get_related_context(
                    clean_text, limit=context_limit
                )
                if context_prefix:
                    self.logger.info(
                        f"已注入相关笔记上下文 ({len(context_prefix)} 字)"
                    )

            # Step 3: 获取 LLM 提供商
            provider = self._get_provider(provider_override)
            self.logger.info(f"使用 LLM: {provider.get_name()}")

            # Step 4: 生成笔记（含质量反馈循环）
            note_text = self._generate_with_quality_loop(
                provider=provider,
                transcript=clean_text,
                chunks=chunks,
                title=title,
                result=result,
                context_prefix=context_prefix,
                mode=mode,
            )

            if note_text is None:
                result.error = "生成失败（已耗尽重试）"
                return result

            # Step 5: 格式化输出
            note_text = self.formatter.format(
                note_text, title, transcript_path,
                mode=mode, content_type=self._content_type or 'lecture',
                transcript_text=clean_text
            )

            # Step 6: 结构校验
            structural_issues = self.formatter.validate_structure(
                note_text, mode=mode, content_type=self._content_type or 'lecture'
            )
            if structural_issues:
                self.logger.warning(
                    f"笔记结构问题: {'; '.join(structural_issues)}"
                )

            # Step 7: 保存笔记
            self._write_file(output_path, note_text)
            result.note_path = output_path
            self.logger.info(f"笔记已保存: {output_path}")

            # Step 8: 最终质量评估
            final_report = self._quality_manager.run_quality_gate(output_path, transcript_path)
            if final_report:
                result.total_score = final_report.get('total_score', 0)
                result.overall_passed = final_report.get('overall_passed', False)
                self._quality_manager.save_quality_report(output_path, final_report)

            # Step 9: 记录 token 使用量
            if hasattr(self._provider, 'get_total_usage'):
                usage = self._provider.get_total_usage()
                result.token_usage = usage
                self.logger.info(
                    f"Token 消耗: input={usage['input_tokens']:,} "
                    f"output={usage['output_tokens']:,} "
                    f"calls={usage['calls']}"
                )

            # Step 10: 飞书知识库同步（可选，失败不阻断）
            self._try_feishu_sync(output_path, note_text)

        except LLMError as e:
            self.logger.error(f"LLM 调用失败: {e}")
            result.error = str(e)
        except Exception as e:
            self.logger.error(f"生成失败: {e}", exc_info=True)
            result.error = str(e)

        result.duration_seconds = time.time() - start_time
        result.attempts = getattr(self, '_current_attempts', 0)
        return result

    def generate_batch(
        self,
        transcript_paths: Optional[List[str]] = None,
        skip_existing: bool = True,
        provider_override: Optional[str] = None,
        force: bool = False,
        mode: str = 'notes',
        with_context: bool = False,
        context_limit: int = 3,
    ) -> List[GenerationResult]:
        """
        批量生成笔记（委托给 BatchProcessor）

        Args:
            transcript_paths: 转写文件路径列表（默认处理所有）
            skip_existing: 是否跳过已有笔记
            provider_override: 覆盖提供商
            force: 是否覆盖已有笔记
            with_context: 是否注入上下文笔记
            context_limit: 上下文笔记数量上限

        Returns:
            结果列表
        """
        return self._batch_processor.generate_batch(
            transcript_paths=transcript_paths,
            generate_note_fn=self.generate_note,
            skip_existing=skip_existing,
            provider_override=provider_override,
            force=force,
            mode=mode,
            with_context=with_context,
            context_limit=context_limit,
        )

    def check_only(self, note_path: str) -> Optional[dict]:
        """
        仅运行质量检查

        Args:
            note_path: 笔记文件路径

        Returns:
            质量报告字典
        """
        transcript_path = self._audio_handler.find_transcript_for_note(note_path)
        if not transcript_path:
            self.logger.error(f"未找到对应的转写文件: {note_path}")
            return None

        return self._quality_manager.check_only(note_path, transcript_path)

    def generate_synthesis(
        self,
        note_paths: Optional[List[str]] = None,
        provider_override: Optional[str] = None,
        domain: Optional[str] = None
    ) -> Optional[str]:
        """
        知识合成模式：读取多篇同域笔记，生成跨集知识框架
        自动按知识域隔离，只合成同域笔记，避免跨领域强行整合。

        Args:
            note_paths: 笔记文件路径列表（默认读取所有）
            provider_override: 覆盖 LLM 提供商
            domain: 指定知识域 ID（默认自动检测最大域）

        Returns:
            合成文档路径，或 None（失败）
        """
        return self._synthesis_engine.generate_synthesis(
            note_paths=note_paths,
            provider=self._get_provider(provider_override),
            prompt_builder=self._get_prompt_builder(),
            domain=domain,
        )

    # ----------------------------------------------------------
    # 两阶段合成：逐集提取 → 合并提炼
    # ----------------------------------------------------------
    def generate_synthesis_two_stage(
        self,
        note_paths: Optional[List[str]] = None,
        provider_override: Optional[str] = None,
        domain: Optional[str] = None
    ) -> Optional[str]:
        """
        两阶段知识合成（域隔离）：
        Stage 1: 逐集提取关键概念（并行，每集独立）
        Stage 2: 合并所有提取结果 + 矛盾检测 → 最终合成文档

        优势：
        - 每集独立提取，不会因上下文过长丢失信息
        - Stage 2 输入更精炼（只有概念，不含全文），token 更少
        - 天然支持增量更新（只重新提取新增集数）
        - 按知识域隔离，避免跨领域强行整合
        """
        return self._synthesis_engine.generate_synthesis_two_stage(
            note_paths=note_paths,
            provider=self._get_provider(provider_override),
            domain=domain,
        )

    # ----------------------------------------------------------
    # 增量更新
    # ----------------------------------------------------------
    def update_synthesis_incremental(
        self,
        new_note_path: str,
        existing_synthesis_path: Optional[str] = None,
        provider_override: Optional[str] = None
    ) -> Optional[str]:
        """
        增量更新知识合成文档（域隔离）：
        1. 检测新笔记的知识域
        2. 查找同域的合成文档
        3. 校验域匹配（不同域拒绝增量更新）
        4. 提取新笔记概念 → 更新同域文档

        Args:
            new_note_path: 新增笔记的路径
            existing_synthesis_path: 现有合成文档路径（默认按域自动查找）
            provider_override: 覆盖 LLM 提供商
        """
        return self._synthesis_engine.update_synthesis_incremental(
            new_note_path=new_note_path,
            provider=self._get_provider(provider_override),
            existing_synthesis_path=existing_synthesis_path,
        )

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _get_related_context(self, content: str, limit: int = 3) -> str:
        """
        获取与当前内容相关的已有笔记上下文（委托给 ExternalSync）

        Args:
            content: 当前转写文本
            limit: 关联笔记数量上限

        Returns:
            格式化的上下文文本，或空字符串
        """
        return self._external_sync.get_related_context(
            content, limit=limit, read_file_fn=self._read_file
        )

    def _try_feishu_sync(self, output_path: str, note_text: str) -> None:
        """
        尝试将笔记同步到飞书知识库（委托给 ExternalSync）。
        失败只 warn，不影响主流程。
        """
        feishu_cfg = self.config.get("feishu", {})
        self._external_sync.try_feishu_sync(output_path, note_text, feishu_cfg)

    def _generate_with_quality_loop(
        self,
        provider: LLMProvider,
        transcript: str,
        chunks: List[str],
        title: str,
        result: GenerationResult,
        context_prefix: str = "",
        mode: str = "notes",
    ) -> Optional[str]:
        """
        带质量反馈的生成循环

        Args:
            context_prefix: 关联笔记上下文前缀（可选）
            mode: 生成模式 ('notes' | 'meeting')

        Returns:
            最终笔记文本，或 None（失败）
        """
        prompt_builder = self._get_prompt_builder()

        # 根据模式选择 prompt
        if mode == 'meeting':
            system_prompt = prompt_builder.build_meeting_system_prompt()
        else:
            system_prompt = prompt_builder.build_system_prompt()
        base_temperature = self.config.get('provider', {}).get(
            self.config.get('provider', {}).get('type', 'claude'), {}
        ).get('temperature', 0.3)

        self._current_attempts = 0

        for attempt in range(1 + self.max_retries):
            self._current_attempts = attempt + 1
            temperature = base_temperature + (attempt * self.retry_temp_delta)

            try:
                if attempt == 0:
                    # 初次生成
                    if len(chunks) == 1:
                        # 注入关联笔记上下文
                        transcript_with_context = chunks[0]
                        if context_prefix:
                            transcript_with_context = (
                                context_prefix + "\n\n---\n\n" + chunks[0]
                            )
                        # 根据模式选择 user prompt
                        if mode == 'meeting':
                            user_prompt = prompt_builder.build_meeting_user_prompt(
                                transcript_with_context, title
                            )
                        else:
                            user_prompt = prompt_builder.build_user_prompt(
                                transcript_with_context, title
                            )
                    else:
                        # 多块：分块生成后合并
                        return self._generate_chunked(
                            provider, prompt_builder, chunks, title,
                            system_prompt, base_temperature, result,
                            context_prefix=context_prefix, mode=mode
                        )

                    self.logger.info(
                        f"调用 LLM (attempt {attempt + 1}, "
                        f"temp={temperature:.1f})..."
                    )
                    note_text = provider.generate(
                        system_prompt, user_prompt,
                        temperature=temperature
                    )
                    self._track_tokens(provider, "generate")
                else:
                    # 重试：使用反馈 prompt
                    self.logger.info(
                        f"质量未达标，重试 {attempt}/{self.max_retries} "
                        f"(temp={temperature:.1f})..."
                    )
                    if self._last_quality_report and self._last_note_text:
                        feedback_prompt = prompt_builder.build_feedback_prompt(
                            transcript, self._last_note_text,
                            self._last_quality_report
                        )
                    else:
                        # 首次调用失败，无法构建反馈 prompt，用原始 prompt 重试
                        self.logger.info("首次调用失败，使用原始 prompt 重试")
                        if mode == 'meeting':
                            feedback_prompt = prompt_builder.build_meeting_user_prompt(
                                transcript, title
                            )
                        else:
                            feedback_prompt = prompt_builder.build_user_prompt(
                                transcript, title, mode=mode
                            )
                    note_text = provider.generate(
                        system_prompt, feedback_prompt,
                        temperature=temperature
                    )
                    self._track_tokens(provider, "retry")

                # 保存中间结果
                self._last_note_text = note_text
                if self.config.get('logging', {}).get('save_intermediate', False):
                    self._quality_manager.save_intermediate(title, attempt, note_text, self.logs_dir)

                # 质量评估
                report = self._quality_manager.run_quality_gate_on_text(
                    note_text, transcript
                )
                self._last_quality_report = report

                if report and report.get('overall_passed', False):
                    self.logger.info(
                        f"质量通过 (score={report['total_score']:.0%}, "
                        f"attempt={attempt + 1})"
                    )
                    return note_text
                elif report:
                    self.logger.warning(
                        f"质量未达标: score={report['total_score']:.0%}, "
                        f"issues={sum(len(r.get('issues', [])) for r in report.get('rule_results', {}).values())}"
                    )
                    if attempt == self.max_retries:
                        self.logger.warning("已达最大重试次数，使用当前版本")
                        return note_text
                else:
                    # quality_gate 不可用，直接接受
                    return note_text

            except LLMError as e:
                if not e.retryable:
                    raise
                self.logger.error(f"LLM 调用失败 (attempt {attempt + 1}): {e}")
                if attempt == self.max_retries:
                    return None

        return None

    def _generate_chunked(
        self,
        provider: LLMProvider,
        prompt_builder: PromptBuilder,
        chunks: List[str],
        title: str,
        system_prompt: str,
        temperature: float,
        result: GenerationResult,
        context_prefix: str = "",
        mode: str = "notes",
    ) -> Optional[str]:
        """分块生成并合并（渐进式摘要：每块的摘要作为下块的上下文）"""
        partial_notes: List[str] = []
        running_summary = ""  # 累积摘要

        for i, chunk in enumerate(chunks):
            self.logger.info(
                f"处理块 {i + 1}/{len(chunks)} "
                f"({len(chunk)} chars)..."
            )

            # 构建带上下文的 prompt
            chunk_parts: List[str] = []

            # 外部上下文（关联笔记）仅注入第一块
            if i == 0 and context_prefix:
                chunk_parts.append(context_prefix)

            # 渐进式摘要上下文
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
            # 根据模式选择 user prompt
            if mode == 'meeting':
                user_prompt = prompt_builder.build_meeting_user_prompt(
                    chunk_with_context, chunk_title
                )
            else:
                user_prompt = prompt_builder.build_user_prompt(
                    chunk_with_context, chunk_title
                )

            try:
                partial = provider.generate(
                    system_prompt, user_prompt,
                    temperature=temperature
                )
                self._track_tokens(provider, "chunk")
                partial_notes.append(partial)

                # 生成本块的摘要，供下一块使用
                if i < len(chunks) - 1:
                    running_summary = self._generate_chunk_summary(
                        provider, system_prompt, partial, running_summary,
                        temperature
                    )
                    self.logger.info(
                        f"块 {i + 1} 摘要: {len(running_summary)} 字"
                    )

            except LLMError as e:
                self.logger.error(f"块 {i + 1} 生成失败: {e}")
                if not e.retryable:
                    raise
                # 可重试错误：跳过此块但记录警告
                self.logger.warning(
                    f"块 {i + 1}/{len(chunks)} 因可重试错误跳过，"
                    f"最终笔记可能不完整"
                )

        if not partial_notes:
            return None

        # 合并所有部分
        if len(partial_notes) == 1:
            return partial_notes[0]

        # 多块合并：拼接各部分，去重头尾
        merged = self._merge_chunk_notes(partial_notes, title)
        return merged

    def _generate_chunk_summary(
        self,
        provider: LLMProvider,
        system_prompt: str,
        chunk_note: str,
        prev_summary: str,
        temperature: float
    ) -> str:
        """
        为单块笔记生成摘要，供下一块作为上下文

        Args:
            provider: LLM 提供商
            system_prompt: 系统 prompt
            chunk_note: 本块生成的笔记
            prev_summary: 前序累积摘要
            temperature: 温度

        Returns:
            更新后的累积摘要
        """
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
            summary = provider.generate(
                system_prompt,
                summary_prompt,
                max_tokens=1024,
                temperature=max(0.1, temperature - 0.1)
            )
            self._track_tokens(provider, "summary")
            return summary
        except LLMError:
            # 摘要失败不影响主流程
            return prev_summary

    def _merge_chunk_notes(self, notes: List[str], title: str) -> str:
        """合并分块生成的笔记"""
        # 简单策略：取第一块的头部（标题+定位），中间拼接，取最后一块的尾部（总结）
        result_parts: List[str] = []

        for i, note in enumerate(notes):
            if i == 0:
                # 第一块：完整保留
                result_parts.append(note)
            else:
                # 后续块：去掉标题和课程定位，只保留内容
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

    def _print_batch_summary(self, results: List[GenerationResult]):



    def _print_batch_summary(self, results: List[GenerationResult]):
        """打印批量处理汇总（委托给 BatchProcessor）"""
        self._batch_processor.print_batch_summary(results)


    @staticmethod
    def _read_file(path: str) -> str:
        """读取文件（尝试 UTF-8，回退 GBK）"""
        for encoding in ('utf-8', 'gbk', 'gb2312'):
            try:
                with open(path, 'r', encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法读取文件（编码问题）: {path}")

    @staticmethod
    def _write_file(path: str, content: str):
        """写入文件（原子写入：先写临时文件再重命名）"""
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp_path, path)


def main():
    """CLI 入口"""
    parser = argparse.ArgumentParser(
        description='NoteForge LLM 笔记生成引擎',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python llm_note_engine.py --input ep01\n"
            "  python llm_note_engine.py --batch --skip-existing\n"
            "  python llm_note_engine.py --input ep01 --force\n"
            "  python llm_note_engine.py --check-only output/notes/ep01.md\n"
        )
    )

    parser.add_argument(
        '--input', nargs='+',
        help='转写文件路径、音频文件路径或集数编号（ep01, ep02, ...）'
    )
    parser.add_argument(
        '--youtube',
        help='YouTube 视频 URL（自动下载音频+转写+生成笔记）'
    )
    parser.add_argument(
        '--youtube-playlist',
        help='YouTube 播放列表 URL（批量下载+转写+生成笔记）'
    )
    parser.add_argument(
        '--bilibili',
        help='Bilibili 视频 URL 或 BV 号（自动下载音频+转写+生成笔记，无需 Cookie）'
    )
    parser.add_argument(
        '--audio-url',
        help='音频平台链接（小宇宙/喜马拉雅/荔枝FM 等，自动下载+转写+生成笔记）'
    )
    # Podcast RSS 订阅
    podcast_group = parser.add_argument_group('Podcast RSS 订阅')
    podcast_group.add_argument(
        '--podcast-subscribe', metavar='URL',
        help='订阅一个 podcast RSS feed（或主页 URL）'
    )
    podcast_group.add_argument(
        '--podcast-unsubscribe', metavar='NAME',
        help='取消订阅一个 podcast feed'
    )
    podcast_group.add_argument(
        '--podcast-list', action='store_true',
        help='列出所有已订阅的 feeds 和 episode 统计'
    )
    podcast_group.add_argument(
        '--podcast-sync', metavar='NAME',
        help='同步指定 feed: 获取新 episodes 列表'
    )
    podcast_group.add_argument(
        '--podcast-sync-all', action='store_true',
        help='同步所有已订阅的 feeds'
    )
    podcast_group.add_argument(
        '--podcast-process', metavar='NAME',
        help='下载+转写+生成笔记: 指定 feed 的所有新 episodes'
    )
    podcast_group.add_argument(
        '--podcast-max', type=int, default=0,
        help='--podcast-process 最多处理的 episode 数 (0=不限)'
    )
    podcast_group.add_argument(
        '--podcast-name', metavar='NAME',
        help='--podcast-subscribe 时手动指定 feed 名称'
    )
    parser.add_argument(
        '--mode', choices=['notes', 'synthesis', 'synthesis-2stage',
                           'synthesis-incremental', 'meeting'], default='notes',
        help='生成模式：notes=单集笔记, synthesis=跨集知识合成, meeting=会议纪要'
    )
    parser.add_argument(
        '--batch', action='store_true',
        help='批量处理所有转写文件'
    )
    parser.add_argument(
        '--skip-existing', action='store_true',
        help='跳过已有笔记的集数'
    )
    parser.add_argument(
        '--force', action='store_true',
        help='覆盖已有笔记'
    )
    parser.add_argument(
        '--check-only',
        help='仅运行质量检查（不生成笔记）'
    )
    parser.add_argument(
        '--config',
        help='自定义配置文件路径'
    )
    parser.add_argument(
        '--provider', choices=['claude', 'openai', 'local'],
        help='覆盖 LLM 提供商'
    )
    parser.add_argument(
        '--output-dir',
        help='覆盖输出目录'
    )
    parser.add_argument(
        '--title',
        help='手动指定笔记标题'
    )
    parser.add_argument(
        '--content-type',
        choices=['lecture', 'tutorial', 'interview', 'podcast', 'meeting'],
        help='内容类型（影响 prompt 策略和质量检查的领域概念加载）'
    )

    # 知识管理
    parser.add_argument(
        '--search',
        help='搜索笔记（关键词）'
    )
    parser.add_argument(
        '--tags', nargs='*',
        help='按标签过滤搜索结果'
    )
    parser.add_argument(
        '--list-notes', action='store_true',
        help='列出所有笔记（含标签和统计）'
    )
    parser.add_argument(
        '--with-context', action='store_true',
        help='生成时自动注入相关历史笔记作为上下文'
    )
    parser.add_argument(
        '--context-limit', type=int, default=3,
        help='关联笔记数量上限（默认 3）'
    )

    parser.add_argument(
        '--verbose', action='store_true',
        help='详细日志输出'
    )
    parser.add_argument(
        '--domain',
        help='指定知识域 ID（用于 synthesis-2stage，只合成该域笔记）'
    )

    args = parser.parse_args()

    # 验证参数
    has_action = (args.input or args.batch or args.check_only or
                  args.youtube or args.youtube_playlist or args.bilibili or args.audio_url or
                  args.mode == 'synthesis' or
                  args.search or args.list_notes or
                  args.podcast_subscribe or args.podcast_unsubscribe or
                  args.podcast_list or args.podcast_sync or
                  args.podcast_sync_all or args.podcast_process)
    if not has_action:
        parser.print_help()
        sys.exit(1)

    # 初始化引擎
    engine = LLMNoteEngine(config_path=args.config)

    if args.content_type:
        engine._content_type = args.content_type

    if args.verbose:
        logging.getLogger('noteforge').setLevel(logging.DEBUG)

    if args.output_dir:
        out = Path(args.output_dir)
        engine.notes_dir = out / 'notes'
        engine.reports_dir = out / 'quality_reports'
        engine.logs_dir = out / 'logs'
        for d in (engine.notes_dir, engine.reports_dir, engine.logs_dir):
            d.mkdir(parents=True, exist_ok=True)
        # 同步更新已提取组件的路径引用
        engine._batch_processor._notes_dir = engine.notes_dir
        engine._external_sync._notes_dir = engine.notes_dir

    # 仅质量检查模式
    if args.check_only:
        if not os.path.exists(args.check_only):
            print(f"[ERROR] 笔记文件不存在: {args.check_only}")
            sys.exit(1)
        report = engine.check_only(args.check_only)
        if report is None:
            print("[ERROR] 质量检查失败（未找到对应转写文件）")
            sys.exit(1)
        sys.exit(0 if report.get('overall_passed') else 1)

    # 笔记搜索
    if args.search:
        from knowledge_index import KnowledgeIndex
        idx = KnowledgeIndex(str(engine.notes_dir))
        results = idx.search(args.search, tags=args.tags)
        if not results:
            print(f"\n未找到匹配 '{args.search}' 的笔记")
        else:
            print(f"\n搜索 '{args.search}' 找到 {len(results)} 条结果:\n")
            for i, r in enumerate(results, 1):
                print(f"  {i}. [{r.date}] {r.title}")
                print(f"     相关度: {r.relevance:.0%} | 标签: {', '.join(r.tags[:5])}")
                print(f"     {r.snippet[:120]}")
                print()
        sys.exit(0)

    # 笔记库概览
    if args.list_notes:
        from knowledge_index import KnowledgeIndex
        idx = KnowledgeIndex(str(engine.notes_dir))
        notes = idx.list_notes()
        tags = idx.get_all_tags()
        if not notes:
            print("\n笔记库为空")
        else:
            print(f"\n{'='*60}")
            print(f"  笔记库概览 ({len(notes)} 篇)")
            print(f"{'='*60}\n")
            for n in notes:
                print(f"  [{n.date}] {n.title}")
                print(f"     {n.char_count} 字 | 框架: {len(n.key_frameworks)} | 行动项: {len(n.action_items)}")
                if n.tags:
                    print(f"     标签: {', '.join(n.tags[:5])}")
            if tags:
                print(f"\n  --- 热门标签 ---")
                tag_str = ' | '.join(f"{t}({c})" for t, c in list(tags.items())[:15])
                print(f"  {tag_str}")
            print(f"\n{'='*60}")
        sys.exit(0)

    # YouTube 单视频模式
    if args.youtube:
        try:
            from youtube_handler import YouTubeHandler
            yt = YouTubeHandler(
                output_dir=str(engine.base_dir / 'output' / 'audio'),
                temp_dir=str(engine.base_dir / 'temp')
            )
            metadata = yt.download_audio(args.youtube)
            audio_path = metadata['path']
            title = args.title or metadata.get('title', '')
            engine.logger.info(f"YouTube 下载完成: {title}")
            result = engine.generate_note(
                audio_path, title=title,
                provider_override=args.provider, force=args.force,
                mode=args.mode,
                with_context=args.with_context,
                context_limit=args.context_limit,
            )
            if result.error:
                print(f"\n[ERROR] {result.error}")
                sys.exit(1)
        except Exception as e:
            print(f"\n[ERROR] YouTube 处理失败: {e}")
            sys.exit(1)
        sys.exit(0)

    # YouTube 播放列表模式
    if args.youtube_playlist:
        try:
            from youtube_handler import YouTubeHandler
            yt = YouTubeHandler(
                output_dir=str(engine.base_dir / 'output' / 'audio'),
                temp_dir=str(engine.base_dir / 'temp')
            )
            results_list = yt.download_playlist(args.youtube_playlist)
            success = [r for r in results_list if 'error' not in r]
            print(f"\n下载完成: {len(success)}/{len(results_list)} 个视频")
            # 对每个下载成功的音频生成笔记
            gen_results = []
            for meta in success:
                r = engine.generate_note(
                    meta['path'],
                    title=meta.get('title', ''),
                    provider_override=args.provider, force=args.force,
                    mode=args.mode,
                    with_context=args.with_context,
                    context_limit=args.context_limit,
                )
                gen_results.append(r)
            engine._print_batch_summary(gen_results)
        except Exception as e:
            print(f"\n[ERROR] YouTube 播放列表处理失败: {e}")
            sys.exit(1)
        sys.exit(0)

    # Bilibili 视频模式
    if args.bilibili:
        try:
            from bilibili_download import download_bilibili
            print(f"\n[Bilibili] 开始处理: {args.bilibili}")
            metadata = download_bilibili(args.bilibili)
            if not metadata.get('success'):
                print(f"\n[ERROR] {metadata.get('error', '下载失败')}")
                engine.logger.error(f"Bilibili 下载失败: {metadata.get('error', '未知')}")
                sys.exit(1)
            audio_path = metadata['path']
            title = args.title or metadata.get('title', '')
            method = metadata.get('method', 'unknown')
            engine.logger.info(f"Bilibili 下载完成: {title} (方法: {method})")
            print(f"  [INFO] 下载方式: {method}")
            result = engine.generate_note(
                audio_path, title=title,
                provider_override=args.provider, force=args.force,
                mode=args.mode,
                with_context=args.with_context,
                context_limit=args.context_limit,
            )
            if result.error and result.error != "已存在（使用 --force 覆盖）":
                print(f"\n[ERROR] {result.error}")
                sys.exit(1)
        except Exception as e:
            print(f"\n[ERROR] Bilibili 处理失败: {e}")
            engine.logger.error(f"Bilibili 处理异常: {e}", exc_info=True)
            sys.exit(1)
        sys.exit(0)

    # 音频平台链接模式（小宇宙/喜马拉雅/荔枝FM 等）
    if args.audio_url:

        output_dir_audio = str(engine.base_dir / 'output' / 'audio')
        os.makedirs(output_dir_audio, exist_ok=True)

        def _try_ytdlp(url, out_dir):
            """尝试 yt-dlp 下载，返回 audio_path 或 None"""
            import shutil
            if not shutil.which('yt-dlp'):
                return None
            output_tpl = os.path.join(out_dir, '%(title)s.%(ext)s')
            dl_cmd = [
                "yt-dlp", "--no-update",
                "--extract-audio", "--audio-format", "mp3",
                "--no-playlist", "-o", output_tpl, url,
            ]
            dl = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=600)
            if dl.returncode != 0:
                return None
            for line in (dl.stdout + dl.stderr).splitlines():
                if '[ExtractAudio]' in line and 'Destination:' in line:
                    p = line.split('Destination:', 1)[1].strip()
                    if os.path.exists(p):
                        return p
            # 回退：找最新 mp3
            import glob as _glob
            candidates = _glob.glob(os.path.join(out_dir, '*.mp3'))
            return max(candidates, key=os.path.getmtime) if candidates else None

        def _try_xiaoyuzhou(url, out_dir):
            """小宇宙 API 提取，返回 (audio_path, title) 或 None"""
            import urllib.request
            m = re.search(r'xiaoyuzhoufm\.com/episode/([a-f0-9]+)', url)
            if not m:
                return None
            eid = m.group(1)
            api = f"https://www.xiaoyuzhoufm.com/api/v1/episode/get?eid={eid}"
            req = urllib.request.Request(api, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            ep = data.get('data', data)
            media = ep.get('media', {})
            audio_url = media.get('src') or ep.get('enclosure', {}).get('url', '')
            if not audio_url:
                return None
            title = ep.get('title', '')
            ext = os.path.splitext(audio_url.split('?')[0])[1] or '.mp3'
            if not ext.startswith('.'):
                ext = '.' + ext
            safe_title = re.sub(r'[\\/:*?"<>|]', '_', title or eid)
            output_path = os.path.join(out_dir, f"{safe_title}{ext}")
            req2 = urllib.request.Request(audio_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://www.xiaoyuzhoufm.com/",
            })
            with urllib.request.urlopen(req2, timeout=300) as resp:
                with open(output_path, 'wb') as f:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
            return (output_path, title) if os.path.exists(output_path) else None

        def _try_lizhi(url, out_dir):
            """荔枝FM API 提取，返回 (audio_path, title) 或 None"""
            import urllib.request
            m = re.search(r'lizhi\.fm/(?:episode/)?(\d+)', url)
            if not m:
                return None
            ep_id = m.group(1)
            api = f"https://www.lizhi.fm/api/audios/episode/{ep_id}"
            req = urllib.request.Request(api, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://www.lizhi.fm/",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            audio_url = data.get('data', {}).get('audio_url', '')
            if not audio_url:
                return None
            title = data.get('data', {}).get('title', '')
            safe_title = re.sub(r'[\\/:*?"<>|]', '_', title or ep_id)
            output_path = os.path.join(out_dir, f"{safe_title}.mp3")
            req2 = urllib.request.Request(audio_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://www.lizhi.fm/",
            })
            with urllib.request.urlopen(req2, timeout=300) as resp:
                with open(output_path, 'wb') as f:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
            return (output_path, title) if os.path.exists(output_path) else None

        # --- 主流程：降级链 ---
        try:
            url = args.audio_url
            audio_path = None
            title = ""

            # 策略 1: yt-dlp（喜马拉雅原生支持，其他平台通用提取）
            print(f"\n  [策略1] yt-dlp 下载: {url}")
            engine.logger.info(f"音频平台: yt-dlp 尝试 {url}")
            result_path = _try_ytdlp(url, output_dir_audio)
            if result_path:
                audio_path = result_path
                title = os.path.splitext(os.path.basename(audio_path))[0]
                print(f"  [OK] yt-dlp 成功")

            # 策略 2: 平台专用 API
            if not audio_path:
                if 'xiaoyuzhoufm.com' in url:
                    print(f"  [策略2] 小宇宙 API 提取...")
                    r = _try_xiaoyuzhou(url, output_dir_audio)
                    if r:
                        audio_path, title = r
                        print(f"  [OK] 小宇宙 API 成功")
                elif 'lizhi.fm' in url:
                    print(f"  [策略2] 荔枝FM API 提取...")
                    r = _try_lizhi(url, output_dir_audio)
                    if r:
                        audio_path, title = r
                        print(f"  [OK] 荔枝FM API 成功")
                elif 'ximalaya.com' in url:
                    # 喜马拉雅仅依赖 yt-dlp（已内置提取器），无 API 降级
                    if '/album/' in url:
                        print(f"  [提示] 喜马拉雅专辑链接不支持，请使用单集 /track/ 链接")
                    else:
                        print(f"  [提示] yt-dlp 不支持该喜马拉雅链接，可能是付费内容或链接格式有误")

            if not audio_path or not os.path.exists(audio_path):
                print(f"\n[ERROR] 所有下载策略均失败。请检查链接是否有效。")
                engine.logger.error(f"音频平台下载失败: {url}")
                sys.exit(1)

            title = args.title or title
            engine.logger.info(f"音频平台: 下载完成 {title}")
            print(f"  音频: {audio_path}")
            result = engine.generate_note(
                audio_path, title=title,
                provider_override=args.provider, force=args.force,
                mode=args.mode,
                with_context=args.with_context,
                context_limit=args.context_limit,
            )
            if result.error:
                print(f"\n[ERROR] {result.error}")
                sys.exit(1)
        except subprocess.TimeoutExpired:
            print("\n[ERROR] 下载超时")
            sys.exit(1)
        except Exception as e:
            print(f"\n[ERROR] 音频平台处理失败: {e}")
            engine.logger.error(f"音频平台处理异常: {e}", exc_info=True)
            sys.exit(1)
        sys.exit(0)

    # 知识合成模式
    if args.mode == 'synthesis':
        note_paths = None
        if args.input:
            # 解析输入为笔记路径
            note_paths = []
            for inp in args.input:
                if os.path.exists(inp):
                    note_paths.append(inp)
                elif inp.startswith('ep'):
                    candidate = engine.notes_dir / f"{inp}.md"
                    if candidate.exists():
                        note_paths.append(str(candidate))

        result = engine.generate_synthesis(
            note_paths=note_paths,
            provider_override=args.provider
        )
        if result:
            print(f"\n[OK] 知识合成文档: {result}")
        else:
            print("\n[ERROR] 知识合成失败")
            sys.exit(1)
        sys.exit(0)

    # 两阶段合成模式
    if args.mode == 'synthesis-2stage':
        note_paths = None
        if args.input:
            note_paths = []
            for inp in args.input:
                if os.path.exists(inp):
                    note_paths.append(inp)

        result = engine.generate_synthesis_two_stage(
            note_paths=note_paths,
            provider_override=args.provider,
            domain=getattr(args, 'domain', None),
        )
        if result:
            print(f"\n[OK] 两阶段合成文档: {result}")
            # 打印 token 统计
            engine.token_manager.print_summary()
        else:
            print("\n[ERROR] 两阶段合成失败")
            sys.exit(1)
        sys.exit(0)

    # 增量更新模式
    if args.mode == 'synthesis-incremental':
        if not args.input:
            print("[ERROR] 增量更新需要指定新增笔记路径 (--input)")
            sys.exit(1)
        new_note = args.input[0]
        if not os.path.exists(new_note):
            # 尝试在 notes 目录查找
            candidate = engine.notes_dir / new_note
            if candidate.exists():
                new_note = str(candidate)
            else:
                print(f"[ERROR] 笔记文件不存在: {new_note}")
                sys.exit(1)

        result = engine.update_synthesis_incremental(
            new_note_path=new_note,
            provider_override=args.provider
        )
        if result:
            print(f"\n[OK] 增量更新完成: {result}")
            engine.token_manager.print_summary()
        else:
            print("\n[ERROR] 增量更新失败")
            sys.exit(1)
        sys.exit(0)

    # Podcast RSS 操作
    podcast_config = str(engine.base_dir / 'config' / 'podcast_feeds.json')
    podcast_audio = str(engine.base_dir / 'output' / 'audio' / 'podcasts')
    podcast_temp = str(engine.base_dir / 'temp')

    if args.podcast_subscribe:
        from podcast_handler import PodcastHandler
        ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
        try:
            info = ph.subscribe(args.podcast_subscribe, name=args.podcast_name)
            print(f"\n[OK] 已订阅: {info['name']}")
            print(f"     Feed URL: {info['feed_url']}")
            print(f"     Episodes: {info['episode_count']}")
        except Exception as e:
            print(f"\n[ERROR] 订阅失败: {e}")
            sys.exit(1)
        sys.exit(0)

    if args.podcast_unsubscribe:
        from podcast_handler import PodcastHandler
        ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
        try:
            ph.unsubscribe(args.podcast_unsubscribe)
            print(f"\n[OK] 已取消订阅: {args.podcast_unsubscribe}")
        except Exception as e:
            print(f"\n[ERROR] {e}")
            sys.exit(1)
        sys.exit(0)

    if args.podcast_list:
        from podcast_handler import PodcastHandler
        ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
        feeds = ph.list_feeds()
        if not feeds:
            print("\n尚未订阅任何 Podcast。使用 --podcast-subscribe URL 添加。")
        else:
            print(f"\n已订阅 {len(feeds)} 个 Podcast:")
            print("-" * 60)
            for f in feeds:
                print(f"  {f['slug']}")
                print(f"    名称: {f['name']}")
                print(f"    Episodes: {f['total_episodes']} "
                      f"(已处理: {f['processed']}, 新: {f['new']})")
                print(f"    最后同步: {f['last_synced'][:19] if f['last_synced'] else '未同步'}")
            print("-" * 60)
        sys.exit(0)

    if args.podcast_sync:
        from podcast_handler import PodcastHandler
        ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
        try:
            config = ph._load_feeds_config()
            if args.podcast_sync not in config['feeds']:
                print(f"\n[ERROR] 未找到订阅: {args.podcast_sync}")
                sys.exit(1)
            episodes = ph.list_episodes(args.podcast_sync, only_new=True)
            feed_name = config['feeds'][args.podcast_sync].get('name', args.podcast_sync)
            total = len(config['feeds'][args.podcast_sync].get('episodes', {}))
            print(f"\n{feed_name}: {len(episodes)}/{total} 个新 episode")
            for i, ep in enumerate(episodes[:20], 1):
                print(f"  {i}. {ep.title[:60]} [{ep.duration}]")
            if len(episodes) > 20:
                print(f"  ... 还有 {len(episodes) - 20} 个")
        except Exception as e:
            print(f"\n[ERROR] 同步失败: {e}")
            sys.exit(1)
        sys.exit(0)

    if args.podcast_sync_all:
        from podcast_handler import PodcastHandler
        ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
        config = ph._load_feeds_config()
        if not config['feeds']:
            print("\n尚未订阅任何 Podcast。")
            sys.exit(0)
        print(f"\n同步 {len(config['feeds'])} 个 Podcast:")
        for slug, feed in config['feeds'].items():
            episodes = ph.list_episodes(slug, only_new=True)
            total = len(feed.get('episodes', {}))
            print(f"  {slug}: {len(episodes)}/{total} 个新 episode")
        sys.exit(0)

    if args.podcast_process:
        from podcast_handler import PodcastHandler
        ph = PodcastHandler(podcast_config, podcast_audio, podcast_temp)
        try:
            # 先同步
            config = ph._load_feeds_config()
            if args.podcast_process not in config['feeds']:
                print(f"\n[ERROR] 未找到订阅: {args.podcast_process}")
                sys.exit(1)
            feed_url = config['feeds'][args.podcast_process]['feed_url']
            ph.subscribe(feed_url, name=args.podcast_process)

            # 下载新 episodes
            episodes = ph.download_new_episodes(args.podcast_process)
            if args.podcast_max > 0:
                episodes = episodes[:args.podcast_max]

            if not episodes:
                print("\n没有新 episode 需要处理。")
                sys.exit(0)

            print(f"\n处理 {len(episodes)} 个 episodes...")
            gen_results = []
            for i, ep in enumerate(episodes, 1):
                engine.logger.info(f"[{i}/{len(episodes)}] {ep.title}")
                result = engine.generate_note(
                    ep.local_audio_path, title=ep.title,
                    provider_override=args.provider, force=args.force,
                    mode=args.mode,
                    with_context=args.with_context,
                    context_limit=args.context_limit,
                )
                if result and not result.error:
                    ph.mark_episode_processed(
                        args.podcast_process, ep.guid,
                        local_audio_path=ep.local_audio_path,
                        note_path=result.note_path
                    )
                gen_results.append(result)
            engine._print_batch_summary(gen_results)
        except Exception as e:
            print(f"\n[ERROR] Podcast 处理失败: {e}")
            sys.exit(1)
        sys.exit(0)

    # 批量模式
    if args.batch:
        if args.title:
            print("[WARN] --title 在批量模式下被忽略")
        results = engine.generate_batch(
            skip_existing=args.skip_existing,
            provider_override=args.provider,
            force=args.force,
            mode=args.mode,
            with_context=args.with_context,
            context_limit=args.context_limit,
        )
        failed = [r for r in results if r.error and r.error != "已存在（跳过）"]
        sys.exit(0 if not failed else 1)

    # 单文件/多文件模式
    if args.input:
        # 解析输入（可能是文件路径或 epXX 编号）
        transcript_paths = []
        for inp in args.input:
            if os.path.exists(inp):
                transcript_paths.append(inp)
            elif inp.startswith('ep'):
                candidate = engine.transcripts_dir / f"{inp}.txt"
                if candidate.exists():
                    transcript_paths.append(str(candidate))
                else:
                    print(f"[ERROR] 未找到转写文件: {candidate}")
            else:
                print(f"[ERROR] 无效输入: {inp}")

        if not transcript_paths:
            print("[ERROR] 没有有效的输入文件")
            sys.exit(1)

        if len(transcript_paths) > 1 and args.title:
            print("[WARN] --title 在多文件输入模式下被忽略")

        if len(transcript_paths) == 1:
            result = engine.generate_note(
                transcript_paths[0],
                provider_override=args.provider,
                force=args.force,
                with_context=args.with_context,
                context_limit=args.context_limit,
                mode=args.mode,
            )
            if result.error and result.error != "已存在（使用 --force 覆盖）":
                print(f"\n[ERROR] {result.error}")
                sys.exit(1)
            if result.total_score > 0:
                engine._quality_manager.print_quality_report(
                    {'total_score': result.total_score,
                     'overall_passed': result.overall_passed,
                     'rule_results': {}}
                )
        else:
            results = engine.generate_batch(
                transcript_paths=transcript_paths,
                skip_existing=not args.force,
                provider_override=args.provider,
                force=args.force,
                mode=args.mode,
                with_context=args.with_context,
                context_limit=args.context_limit,
            )
            failed = [r for r in results if r.error and "已存在" not in r.error]
            sys.exit(0 if not failed else 1)


if __name__ == '__main__':
    main()
