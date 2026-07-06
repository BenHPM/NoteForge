"""
NoteForge LLM 笔记生成引擎 v1.0
主控模块：串联预处理→prompt→LLM→格式化→质量门禁→重试循环

用法:
    python cli.py --input ep01
    python cli.py --batch --skip-existing
    python cli.py --input ep01 --force
    python cli.py --check-only note.md
"""

import os
import sys
import time
import logging
from pathlib import Path
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
from logging_config import setup_logging

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
            content_type=self._content_type,
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
        log_dir = str(self.base_dir / 'output' / 'logs')
        root = setup_logging(level=level, log_dir=log_dir)
        # 文件 handler 使用 DEBUG 级别以记录更多细节
        for h in root.handlers:
            if isinstance(h, logging.FileHandler):
                h.setLevel(logging.DEBUG)

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

            # 自动创建中文名副本（当标题含中文但输出路径为ASCII时）
            # 解决命令行中文引号等特殊字符路径问题
            output_stem = Path(output_path).stem
            if title and title != output_stem:
                if any(ord(c) > 127 for c in title) and not any(ord(c) > 127 for c in output_stem):
                    chinese_path = str(Path(output_path).parent / f"{title}.md")
                    if not os.path.exists(chinese_path):
                        import shutil
                        shutil.copy2(output_path, chinese_path)
                        self.logger.info(f"中文名副本: {chinese_path}")

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

            # Step 11: 自动触发跨集知识合成（同域笔记新增时）
            self._auto_trigger_synthesis(output_path)

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

    def _auto_trigger_synthesis(self, note_path: str) -> None:
        """
        笔记生成成功后，自动检测同域笔记数量变化，触发跨集知识合成。
        条件：同域笔记 >= 3 篇 且 auto_synthesis 配置启用。
        失败只 warn，不影响主流程。
        """
        # 检查是否启用自动合成
        synthesis_cfg = self.config.get("synthesis", {})
        if not synthesis_cfg.get("auto_trigger", False):
            return

        # 检测笔记所属域
        note_stem = Path(note_path).stem
        domain_id = self._domain_classifier.detect_domain(note_stem)
        if domain_id == "general":
            return  # general 域不触发合成

        # 检查同域笔记数量
        domain_notes = self._domain_classifier.get_notes_by_domain().get(domain_id, [])
        min_notes = synthesis_cfg.get("auto_trigger_min_notes", 3)
        if len(domain_notes) < min_notes:
            self.logger.info(f"域 '{domain_id}' 有 {len(domain_notes)} 篇笔记，"
                           f"未达自动合成阈值 ({min_notes})")
            return

        # 触发两阶段合成
        self.logger.info(f"域 '{domain_id}' 有 {len(domain_notes)} 篇笔记，"
                        f"自动触发跨集知识合成...")
        try:
            result = self.generate_synthesis_two_stage(domain=domain_id)
            if result:
                self.logger.info(f"自动合成完成: {result}")
                # 同步合成结果到飞书
                self._try_feishu_sync(result, "")
            else:
                self.logger.warning("自动合成失败（不影响笔记生成结果）")
        except Exception as e:
            self.logger.warning(f"自动合成异常: {e}（不影响笔记生成结果）")

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
                        # 重试时保留 context_prefix，与首次生成一致
                        retry_transcript = transcript
                        if context_prefix and len(chunks) == 1:
                            retry_transcript = (
                                context_prefix + "\n\n---\n\n" + chunks[0]
                            )
                        if mode == 'meeting':
                            feedback_prompt = prompt_builder.build_meeting_user_prompt(
                                retry_transcript, title
                            )
                        else:
                            feedback_prompt = prompt_builder.build_user_prompt(
                                retry_transcript, title, mode=mode
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
