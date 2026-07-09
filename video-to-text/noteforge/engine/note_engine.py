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
import time
import logging
from pathlib import Path
from typing import List, Optional, Dict


BASE_DIR = Path(__file__).parent.parent.parent  # noteforge/engine/ -> video-to-text/

from noteforge.infra import env as env_check  # noqa: F401 — 检测 Python 环境（必须在其他 import 之前）
from noteforge.infra.logging_setup import setup_logging

from noteforge.config import NoteForgeConfig
from noteforge.core.transcript_preprocessor import TranscriptPreprocessor
from noteforge.core.prompt_builder import PromptBuilder
from noteforge.core.note_formatter import NoteFormatter
from noteforge.core.llm_providers import create_provider, LLMProvider, LLMError
from noteforge.core.token_manager import TokenManager, TokenUsage
from noteforge.context import PipelineContext
from noteforge.models import GenerationResult
from noteforge.infra.file_io import read_file
from noteforge.core.domain_classifier import DomainClassifier
from noteforge.intelligence.synthesis import SynthesisEngine
from noteforge.core.audio_handler import AudioHandler
from noteforge.quality.manager import QualityManager
from noteforge.batch.processor import BatchProcessor
from noteforge.integration.sync import ExternalSync
from noteforge.engine.pipeline import Pipeline
from noteforge.engine.stages.generate import GenerateStage
from noteforge.engine.stages.preprocess import PreprocessStage
from noteforge.engine.stages.format import FormatStage
from noteforge.engine.stages.save import SaveStage
from noteforge.engine.stages.evaluate import QualityGateStage
from noteforge.engine.stages.postprocess import PostProcessStage



class LLMNoteEngine:
    """LLM 笔记生成引擎"""

    def __init__(self, config_path: Optional[str] = None):
        """
        Args:
            config_path: 配置文件路径。默认使用 config/llm_engine_config.yaml
        """
        if config_path is None:
            config_path = str(BASE_DIR / "config" / "llm_engine_config.yaml")

        self.config_mgr = NoteForgeConfig(config_path=config_path, base_dir=BASE_DIR)
        self.config = self.config_mgr.raw
        self._path_config = self.config_mgr.path_config

        self.logger = logging.getLogger('noteforge.engine')
        self.preprocessor = TranscriptPreprocessor()
        self.formatter = NoteFormatter()

        # 便利属性（委托到 _path_config）
        self._setup_logging()

        # 确保输出目录存在
        self._path_config.notes_dir.mkdir(parents=True, exist_ok=True)
        self._path_config.reports_dir.mkdir(parents=True, exist_ok=True)
        self._path_config.logs_dir.mkdir(parents=True, exist_ok=True)

        # Token 使用追踪
        self.token_manager = TokenManager(log_dir=str(self._path_config.logs_dir))

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
            domains=domains, path_config=self._path_config,
        )
        # 音频处理器
        self._audio_handler = AudioHandler(
            transcripts_dir=self._path_config.transcripts_dir,
            base_dir=self._path_config.base_dir,
            logger=self.logger,
        )
        # 质量管理器
        self.quality_manager = QualityManager(
            path_config=self._path_config,
            logger=self.logger,
            config=self.config,
            content_type=self._content_type,
        )
        # 知识合成引擎
        self._synthesis_engine = SynthesisEngine(
            domain_classifier=self._domain_classifier,
            path_config=self._path_config,
            logger=self.logger,
            track_tokens_fn=self._track_tokens,
        )
        # 批量处理器
        self._batch_processor = BatchProcessor(
            path_config=self._path_config,
            logger=self.logger,
            token_manager=self.token_manager,
        )
        # 外部同步处理器
        self._external_sync = ExternalSync(
            path_config=self._path_config,
            logger=self.logger,
        )
        # LLM 提供商（延迟初始化）
        self._provider: Optional[LLMProvider] = None
        # 合成冷却期（domain_id -> 上次合成时间戳）
        self._last_synthesis_time: Dict[str, float] = {}
        # 延迟合成：记录有待合成新笔记的域（不立即执行，由调用方决定何时触发）
        self._pending_synthesis_domains: set = set()

    # 知识域代理属性（委托给 DomainClassifier）
    # 路径便利属性（委托给 _path_config）
    @property
    def base_dir(self) -> Path:
        return self._path_config.base_dir

    @property
    def notes_dir(self) -> Path:
        return self._path_config.notes_dir

    @property
    def transcripts_dir(self) -> Path:
        return self._path_config.transcripts_dir

    @property
    def reports_dir(self) -> Path:
        return self._path_config.reports_dir

    @property
    def logs_dir(self) -> Path:
        return self._path_config.logs_dir

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

    def configure(self, content_type: Optional[str] = None,
                  output_dir: Optional[str] = None) -> None:
        """
        配置引擎参数（替代直接修改 engine._xxx 属性）

        Args:
            content_type: 内容类型（lecture/tutorial/interview/podcast/meeting）
            output_dir: 覆盖输出目录
        """
        if content_type is not None:
            self._content_type = content_type
            self.quality_manager._content_type = content_type
            # 重置 prompt builder 以使用新的 content_type
            self._prompt_builder = None

        if output_dir is not None:
            out = Path(output_dir)
            self._path_config.notes_dir = out / 'notes'
            self._path_config.reports_dir = out / 'quality_reports'
            self._path_config.logs_dir = out / 'logs'
            for d in (self._path_config.notes_dir, self._path_config.reports_dir,
                      self._path_config.logs_dir):
                d.mkdir(parents=True, exist_ok=True)

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
        生成单篇笔记（Pipeline 编排）

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
            transcript_path = self._audio_handler.transcribe_audio(
                transcript_path, result, force_retranscribe=force
            )
            if transcript_path is None:
                result.error = "音频转写失败"
                return result

        try:
            # 获取 LLM 提供商（在 Pipeline 外初始化，供多个 stage 共享）
            provider = self._get_provider(provider_override)
            self.logger.info(f"使用 LLM: {provider.get_name()}")

            # 构建 PipelineContext
            from noteforge.context import PipelineContext
            ctx = PipelineContext(
                source_path=transcript_path,
                output_path=output_path,
                title=title,
                content_type=self._content_type or 'lecture',
                mode=mode,
                force=force,
                with_context=with_context,
                context_limit=context_limit,
                transcript_path=transcript_path,
                batch_mode=getattr(self, '_batch_mode', False),
            )

            # 构建并执行 Pipeline
            pipeline = Pipeline([
                PreprocessStage(
                    preprocessor=self.preprocessor,
                    transcript_config=self.config.get('transcript', {}),
                    get_related_context_fn=self._get_related_context,
                    logger=self.logger,
                ),
                GenerateStage(
                    prompt_builder=self._get_prompt_builder(),
                    quality_manager=self.quality_manager,
                    provider=provider,
                    track_tokens_fn=self._track_tokens,
                    max_retries=self.max_retries,
                    retry_temp_delta=self.retry_temp_delta,
                    base_temperature=self.config.get('provider', {}).get(
                        self.config.get('provider', {}).get('type', 'claude'), {}
                    ).get('temperature', 0.3),
                    save_intermediate=self.config.get('logging', {}).get('save_intermediate', False),
                    logs_dir=str(self.logs_dir),
                    min_score=self.min_score,
                ),
                FormatStage(
                    formatter=self.formatter,
                    content_type=self._content_type or 'lecture',
                    logger=self.logger,
                ),
                SaveStage(
                    notes_dir=self.notes_dir,
                    logger=self.logger,
                ),
                QualityGateStage(
                    quality_manager=self.quality_manager,
                    reports_dir=self.reports_dir,
                    logger=self.logger,
                ),
                PostProcessStage(
                    get_total_usage_fn=(
                        provider.get_total_usage
                        if hasattr(provider, 'get_total_usage')
                        else None
                    ),
                    try_feishu_sync_fn=self._try_feishu_sync,
                    auto_trigger_synthesis_fn=self._auto_trigger_synthesis,
                    logger=self.logger,
                ),
            ])

            ctx = pipeline.run(ctx)

            # 从 ctx 构建返回值
            if ctx.error:
                result.error = ctx.error
            else:
                result.note_path = output_path
                if ctx.quality_report:
                    result.total_score = ctx.total_score
                    result.overall_passed = ctx.overall_passed
                result.token_usage = ctx.token_usage

            self._current_attempts = ctx.attempts

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

        批量模式下，单篇自动合成被延迟（_auto_trigger_synthesis 只记录域），
        批量完成后统一调用 flush_pending_synthesis() 触发一次合成。

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
        # 标记批量模式，让 PostProcessStage 跳过单篇自动合成
        self._batch_mode = True
        try:
            results = self._batch_processor.generate_batch(
                transcript_paths=transcript_paths,
                generate_note_fn=self.generate_note,
                skip_existing=skip_existing,
                provider_override=provider_override,
                force=force,
                mode=mode,
                with_context=with_context,
                context_limit=context_limit,
            )

            # 批量完成后，统一触发一次合成
            self.flush_pending_synthesis()
        finally:
            self._batch_mode = False

        return results

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

        return self.quality_manager.check_only(note_path, transcript_path)

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
            content, limit=limit, read_file_fn=read_file
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
        笔记生成成功后，记录同域笔记变化，延迟触发跨集知识合成。
        只将当前笔记的域加入 _pending_synthesis_domains，不立即执行合成。
        由调用方通过 flush_pending_synthesis() 决定何时统一触发。
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

        # 记录待合成域（不立即执行）
        self._pending_synthesis_domains.add(domain_id)
        self.logger.info(f"域 '{domain_id}' 已记录待合成（将在 flush 时统一触发）")

    def flush_pending_synthesis(self) -> None:
        """
        统一触发所有待合成域的跨集知识合成。
        单文件模式：generate_note 后立即调用（行为不变）。
        批量模式：所有笔记生成完成后统一调用一次（避免重复触发）。
        """
        if not self._pending_synthesis_domains:
            return

        synthesis_cfg = self.config.get("synthesis", {})
        if not synthesis_cfg.get("auto_trigger", False):
            self._pending_synthesis_domains.clear()
            return

        # 检查冷却期
        cooldown = synthesis_cfg.get("auto_trigger_cooldown_seconds", 1800)
        min_notes = synthesis_cfg.get("auto_trigger_min_notes", 3)

        # 复制后清空，避免合成过程中重复添加导致无限循环
        pending = set(self._pending_synthesis_domains)
        self._pending_synthesis_domains.clear()

        for domain_id in pending:
            # 检查冷却期
            last_time = self._last_synthesis_time.get(domain_id, 0)
            if time.time() - last_time < cooldown:
                self.logger.info(f"域 '{domain_id}' 合成冷却中，跳过")
                continue

            # 检查同域笔记数量
            domain_notes = self._domain_classifier.get_notes_by_domain().get(domain_id, [])
            if len(domain_notes) < min_notes:
                self.logger.info(
                    f"域 '{domain_id}' 有 {len(domain_notes)} 篇笔记，"
                    f"未达自动合成阈值 ({min_notes})"
                )
                continue

            # 触发两阶段合成
            self.logger.info(
                f"域 '{domain_id}' 有 {len(domain_notes)} 篇笔记，"
                f"触发跨集知识合成..."
            )
            try:
                result = self.generate_synthesis_two_stage(domain=domain_id)
                if result:
                    self.logger.info(f"自动合成完成: {result}")
                    self._last_synthesis_time[domain_id] = time.time()
                    self._try_feishu_sync(result, "")
                else:
                    self.logger.warning("自动合成失败（不影响笔记生成结果）")
            except Exception as e:
                self.logger.warning(f"自动合成异常: {e}（不影响笔记生成结果）")

    def print_batch_summary(self, results: List[GenerationResult]):
        """打印批量处理汇总（委托给 BatchProcessor）"""
        self._batch_processor.print_batch_summary(results)

    # 兼容旧调用（CLI 中仍有 engine._print_batch_summary 引用）
    _print_batch_summary = print_batch_summary


