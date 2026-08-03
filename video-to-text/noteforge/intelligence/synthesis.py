# -*- coding: utf-8 -*-
"""
NoteForge 知识合成引擎
从 llm_note_engine.py 提取的合成相关方法，独立为可测试的模块。

职责：
  - 单次合成 (generate_synthesis)
  - 两阶段合成 (generate_synthesis_two_stage)
  - 增量更新 (update_synthesis_incremental)
  - 矛盾检测 (_detect_contradictions)
  - Prompt 构建委托 → noteforge.intelligence.prompts
  - 合成质量验证委托 → noteforge.intelligence.validation
"""

import os
import logging
from pathlib import Path
from typing import List, Optional, Callable, Tuple

from noteforge.core.llm_providers import LLMProvider, LLMError
from noteforge.core.note_value import is_low_value_note
from noteforge.infra.file_io import read_file, write_file
from noteforge.intelligence.prompts import (
    build_synthesis_system_prompt,
    build_synthesis_prompt,
    build_synthesis_prompt_with_index,
    build_extraction_prompt,
    build_merge_prompt,
    build_incremental_update_prompt,
    build_contradiction_detection_prompt,
)
from noteforge.intelligence.validation import (
    validate_synthesis, is_critical_issue, detect_cot_leak,
)
from noteforge.intelligence.knowledge_index import KnowledgeIndex

# 排除的笔记文件前缀（合成/提取/矛盾报告等非原始笔记）
_EXCLUDED_PREFIXES = ('knowledge_', 'mental_models', 'action_playbook',
                      'extraction_', 'contradictions_')

# 排除的子串（中文命名的合成文档与质量报告，非原始笔记）
_EXCLUDED_SUBSTRINGS = ('知识体系', '质量报告', 'quality_report')

# 中间版本后缀（_v2/_v3/_v4 是迭代中间产物，同视频冗余；_v5 为最终版应保留；
# _2stage/_incremental/_contradictions 是两阶段/增量/矛盾报告中间产物）
_EXCLUDED_VERSION_SUFFIXES = ('_v2', '_v3', '_v4', '_2stage', '_incremental',
                              '_contradictions')


class SynthesisEngine:
    """知识合成引擎（独立于 LLMNoteEngine）"""

    def __init__(
        self,
        domain_classifier,
        path_config,
        logger: logging.Logger,
        track_tokens_fn: Optional[Callable] = None,
        notes_dir: Path = None,
        base_dir: Path = None,
    ):
        """
        Args:
            domain_classifier: DomainClassifier 实例
            path_config: PathConfig 共享路径配置（持有引用，路径变更自动同步）
            logger: 日志记录器
            track_tokens_fn: token 追踪回调（可选）
            notes_dir: 笔记目录（已废弃，优先使用 path_config.notes_dir）
            base_dir: 项目根目录（已废弃，优先使用 path_config.base_dir）
        """
        self._domain_classifier = domain_classifier
        self._path_config = path_config
        self.logger = logger
        self._track_tokens_fn = track_tokens_fn

    # 兼容属性（委托到 _path_config）
    @property
    def _notes_dir(self):
        return self._path_config.notes_dir

    @property
    def _base_dir(self):
        return self._path_config.base_dir

    # --- 公开接口 ---

    def generate_synthesis(
        self,
        note_paths: Optional[List[str]] = None,
        provider: Optional[LLMProvider] = None,
        domain: Optional[str] = None,
    ) -> Optional[str]:
        """知识合成模式：读取多篇同域笔记，生成跨集知识框架"""
        if provider is None:
            self.logger.error("generate_synthesis: provider 不能为 None")
            return None

        note_paths, domain, domain_cfg = self._resolve_domain(note_paths, domain)
        if note_paths is None or not note_paths:
            return None

        self.logger.info(f"知识合成: {len(note_paths)} 篇笔记")

        # 读取所有笔记内容
        notes_content = self._read_notes(note_paths)
        if not notes_content:
            return None

        all_notes = "\n\n---\n\n".join(notes_content)
        system_prompt = build_synthesis_system_prompt()

        # 注入知识索引上下文（非侵入式：无数据时不影响原有 prompt）
        index_context = self._get_index_context(note_paths, domain)
        synthesis_prompt = build_synthesis_prompt_with_index(all_notes, index_context)

        self.logger.info("调用 LLM 生成知识合成文档...")
        try:
            synthesis_text = provider.generate(
                system_prompt, synthesis_prompt, max_tokens=16384
            )
            self._track_tokens(provider, "synthesis")
        except LLMError as e:
            self.logger.error(f"合成生成失败: {e}")
            return None

        synthesis_text, _ = self._validate_and_maybe_retry(
            provider, system_prompt, synthesis_prompt, synthesis_text,
            note_paths, purpose="synthesis", temperature=0.3,
        )
        return self._save_synthesis(synthesis_text, domain_cfg)

    def generate_synthesis_two_stage(
        self,
        note_paths: Optional[List[str]] = None,
        provider: Optional[LLMProvider] = None,
        domain: Optional[str] = None,
    ) -> Optional[str]:
        """两阶段知识合成（域隔离）：逐集提取 → 合并 + 矛盾检测"""
        if provider is None:
            self.logger.error("generate_synthesis_two_stage: provider 不能为 None")
            return None

        note_paths, domain, domain_cfg = self._resolve_domain(note_paths, domain)
        if not note_paths:
            self.logger.warning("未找到笔记文件")
            return None

        # 检查是否已有合成文档且无新笔记
        output_name = domain_cfg.get('output_name', 'knowledge_synthesis')
        synthesis_path = str(self._notes_dir / f"{output_name}.md")
        if os.path.exists(synthesis_path):
            synth_mtime = os.path.getmtime(synthesis_path)
            new_notes = [p for p in note_paths if os.path.getmtime(p) > synth_mtime]
            if not new_notes:
                self.logger.info(f"域 '{domain_cfg.get('name', domain)}' 合成文档已是最新，跳过")
                return synthesis_path
            self.logger.info(f"域 '{domain_cfg.get('name', domain)}' 有 {len(new_notes)} 篇新笔记，重新合成")

        system_prompt = build_synthesis_system_prompt()
        extractions_dir = self._notes_dir / "extractions"
        extractions_dir.mkdir(parents=True, exist_ok=True)

        # === Stage 1: 逐集提取 ===
        all_extractions = self._run_extractions(note_paths, provider, system_prompt, extractions_dir)
        if not all_extractions:
            self.logger.error("[Stage 1] 所有集数提取失败")
            return None

        # === Stage 2: 合并提炼 + 矛盾检测 ===
        self.logger.info(f"[Stage 2] 合并提炼: {len(all_extractions)} 份提取结果")
        merged_extractions = "\n\n---\n\n".join(all_extractions)
        contradictions = self._detect_contradictions(merged_extractions, provider)

        # 注入知识索引上下文（非侵入式：无数据时不影响原有 prompt）
        index_context = self._get_index_context(note_paths, domain)
        merge_prompt = build_merge_prompt(merged_extractions, contradictions, index_context)

        self.logger.info("[Stage 2] 生成最终合成文档...")
        try:
            synthesis_text = provider.generate(
                system_prompt, merge_prompt, max_tokens=16384, temperature=0.3
            )
            self._track_tokens(provider, "synthesis_merge")
        except LLMError as e:
            self.logger.error(f"[Stage 2] 合成失败: {e}")
            return None

        synthesis_text, _ = self._validate_and_maybe_retry(
            provider, system_prompt, merge_prompt, synthesis_text,
            note_paths, purpose="synthesis_merge", temperature=0.3,
        )
        synthesis_path = self._save_synthesis(synthesis_text, domain_cfg)

        # 保存矛盾检测报告
        if contradictions:
            contradictions_path = str(self._notes_dir / f"{output_name}_contradictions.md")
            write_file(contradictions_path, contradictions)
            self.logger.info(f"矛盾检测报告: {contradictions_path}")

        self.logger.info(f"合成文档已保存: {synthesis_path}")
        return synthesis_path

    def update_synthesis_incremental(
        self,
        new_note_path: str,
        provider: Optional[LLMProvider] = None,
        existing_synthesis_path: Optional[str] = None,
    ) -> Optional[str]:
        """增量更新知识合成文档（域隔离）"""
        if provider is None:
            self.logger.error("update_synthesis_incremental: provider 不能为 None")
            return None

        # 检测新笔记的知识域
        note_domain = self._domain_classifier.detect_domain(new_note_path)
        domain_cfg = self._domain_classifier.get_domain_config(note_domain)
        self.logger.info(f"新笔记域: {domain_cfg.get('name', note_domain)}")

        # 按域查找匹配的合成文档
        existing_synthesis_path = self._find_existing_synthesis(
            existing_synthesis_path, domain_cfg
        )

        if not existing_synthesis_path or not Path(existing_synthesis_path).exists():
            self.logger.warning(f"未找到域 '{domain_cfg.get('name', note_domain)}' 的合成文档，将执行全量合成")
            return self.generate_synthesis_two_stage(
                note_paths=[new_note_path], provider=provider, domain=note_domain
            )

        # 域匹配校验
        is_match, note_dom, synth_dom = self._domain_classifier.validate_domain_match(
            new_note_path, existing_synthesis_path
        )
        if not is_match:
            self.logger.warning(
                f"域不匹配: 新笔记属于 '{self._domain_classifier.get_domain_config(note_dom).get('name', note_dom)}'，"
                f"但合成文档属于 '{self._domain_classifier.get_domain_config(synth_dom).get('name', synth_dom)}'。"
                f"将为新笔记创建独立的域合成文档。"
            )
            return self.generate_synthesis_two_stage(
                note_paths=[new_note_path], provider=provider, domain=note_domain
            )

        system_prompt = build_synthesis_system_prompt()
        new_content = read_file(new_note_path)
        existing_synthesis = read_file(existing_synthesis_path)
        new_stem = Path(new_note_path).stem
        self.logger.info(f"增量更新: {new_stem} → {Path(existing_synthesis_path).name}")

        # 提取新笔记的关键概念
        extraction_prompt = build_extraction_prompt(new_stem, new_content)
        try:
            new_extraction = provider.generate(
                system_prompt, extraction_prompt, max_tokens=2048, temperature=0.2
            )
            self._track_tokens(provider, "incremental_extraction")
        except LLMError as e:
            self.logger.error(f"新笔记提取失败: {e}")
            return None

        # 增量更新
        update_prompt = build_incremental_update_prompt(new_stem, new_extraction, existing_synthesis)
        try:
            updated_synthesis = provider.generate(
                system_prompt, update_prompt, max_tokens=16384, temperature=0.3
            )
            self._track_tokens(provider, "incremental_update")
        except LLMError as e:
            self.logger.error(f"增量更新失败: {e}")
            return None

        # 验证（严重问题带反馈重试一次）
        all_notes = self._collect_note_paths()
        updated_synthesis, _ = self._validate_and_maybe_retry(
            provider, system_prompt, update_prompt, updated_synthesis,
            all_notes, purpose="incremental_update", temperature=0.3,
        )

        # 保存
        output_name = domain_cfg.get('output_name', 'knowledge_synthesis')
        synthesis_path = str(self._notes_dir / f"{output_name}.md")
        write_file(synthesis_path, updated_synthesis)
        self.logger.info(f"增量更新完成: {synthesis_path}")
        return synthesis_path

    # --- 矛盾检测 ---

    def _detect_contradictions(self, extractions: str,
                                provider: LLMProvider) -> str:
        """从各集提取结果中检测矛盾和张力"""
        self.logger.info("[矛盾检测] 分析各集提取结果中的潜在矛盾...")
        contradiction_prompt = build_contradiction_detection_prompt(extractions)

        try:
            result = provider.generate(
                "你是一位批判性思维分析师，擅长发现不同观点之间的矛盾和张力。",
                contradiction_prompt, max_tokens=4096, temperature=0.2
            )
            self._track_tokens(provider, "contradiction_detection")

            if "未发现明显矛盾" in result:
                self.logger.info("[矛盾检测] 未发现明显矛盾")
                return ""

            self.logger.info("[矛盾检测] 发现潜在矛盾，详见报告")
            return result
        except LLMError as e:
            self.logger.warning(f"[矛盾检测] 检测失败: {e}")
            return ""

    # --- 内部工具 ---

    def _get_index_context(self, note_paths: List[str], domain: str) -> dict:
        """从知识索引获取上下文，注入合成 prompt

        Args:
            note_paths: 当前合成的笔记路径列表
            domain: 知识域 ID

        Returns:
            包含 related_titles / existing_frameworks / existing_tags 的 dict，
            无数据时返回空 dict。
        """
        try:
            index = KnowledgeIndex(notes_dir=str(self._notes_dir))
            index.build_index()

            current_stems = {Path(p).stem for p in note_paths}

            # 按域收集已有笔记，排除当前正在合成的
            domain_notes: List = []
            all_tags: Dict[str, int] = {}
            existing_frameworks: set = set()

            for summary in index.list_notes():
                if Path(summary.path).stem in current_stems:
                    continue

                # 域匹配：优先按文件名关键词 + 标签重叠双重判断
                note_dom = self._domain_classifier.detect_domain(summary.path)
                if note_dom != domain:
                    continue

                domain_notes.append(summary)
                for tag in summary.tags:
                    all_tags[tag] = all_tags.get(tag, 0) + 1
                existing_frameworks.update(summary.key_frameworks)

            related_titles = [s.title for s in domain_notes]

            ctx: dict = {}
            if related_titles:
                ctx['related_titles'] = related_titles[:10]
            if existing_frameworks:
                ctx['existing_frameworks'] = sorted(existing_frameworks)[:15]
            if all_tags:
                ctx['existing_tags'] = all_tags
            return ctx
        except Exception as e:
            self.logger.debug(f"知识索引构建失败（不影响合成）: {e}")
            return {}

    def _track_tokens(self, provider: LLMProvider, purpose: str = "generate"):
        """从 provider 读取 token 使用量并记录（通过回调）"""
        if self._track_tokens_fn is not None:
            self._track_tokens_fn(provider, purpose)

    def _collect_note_paths(self) -> List[str]:
        """收集笔记目录下的所有原始笔记路径（排除合成/提取等非原始笔记 + 中间版本 + 低价值）"""
        result: List[str] = []
        for p in sorted(self._notes_dir.glob('*.md')):
            if self._is_non_note(Path(p).stem):
                continue
            # 低价值笔记（招生简章/上线通知/无知识可提炼）不进跨集合成源
            try:
                preview = read_file(str(p))[:3000]
            except Exception:
                preview = ""
            if is_low_value_note(p.name, preview):
                self.logger.info(f"跳过低价值笔记（不参与合成）: {p.name}")
                continue
            result.append(str(p))
        return result

    @staticmethod
    def _is_non_note(stem: str) -> bool:
        """判断是否为非原始笔记（与 DomainClassifier._is_non_note 保持一致）。

        排除规则：
        1. 前缀：knowledge_/mental_models/action_playbook/extraction_/contradictions_
        2. 子串：知识体系 / 质量报告（中文命名的合成文档与质量报告）
        3. 中间版本后缀：_v2/_v3/_v4（同视频冗余中间产物，_v5 为最终版应保留）
           + _2stage/_incremental/_contradictions（两阶段/增量中间产物）
        """
        if stem.startswith(_EXCLUDED_PREFIXES):
            return True
        if any(sub in stem for sub in _EXCLUDED_SUBSTRINGS):
            return True
        for suffix in _EXCLUDED_VERSION_SUFFIXES:
            if stem.endswith(suffix):
                return True
        return False

    def _resolve_domain(self, note_paths: Optional[List[str]], domain: Optional[str],
                        ) -> Tuple[Optional[List[str]], str, dict]:
        """解析知识域：收集笔记 → 按域分组 → 返回 (note_paths, domain, domain_cfg)"""
        if note_paths is None:
            note_paths = self._collect_note_paths()

        if not note_paths:
            self.logger.warning("未找到笔记文件")
            return None, domain or 'general', {}

        groups = self._domain_classifier.get_notes_by_domain(note_paths)
        if len(groups) > 1:
            self.logger.info(f"检测到 {len(groups)} 个知识域，按域隔离合成:")
            for did, paths in groups.items():
                cfg = self._domain_classifier.get_domain_config(did)
                self.logger.info(f"  {cfg.get('name', did)}: {len(paths)} 篇")

        if domain:
            note_paths = groups.get(domain, note_paths)
        else:
            domain = max(groups.keys(), key=lambda k: len(groups[k])) if groups else 'general'
            note_paths = groups.get(domain, note_paths)

        domain_cfg = self._domain_classifier.get_domain_config(domain)
        self.logger.info(f"合成域: {domain_cfg.get('name', domain)} ({len(note_paths)} 篇)")
        return note_paths, domain, domain_cfg

    def _read_notes(self, note_paths: List[str]) -> List[str]:
        """读取笔记文件内容，返回格式化列表 ['### stem\\n\\ncontent', ...]"""
        notes_content: List[str] = []
        for path in note_paths:
            try:
                content = read_file(path)
                stem = Path(path).stem
                notes_content.append(f"### {stem}\n\n{content}")
            except Exception as e:
                self.logger.warning(f"读取失败 {path}: {e}")
        return notes_content

    def _run_extractions(self, note_paths: List[str], provider: LLMProvider,
                         system_prompt: str, extractions_dir: Path) -> List[str]:
        """Stage 1: 逐集提取关键概念，返回提取结果列表"""
        self.logger.info(f"[Stage 1] 逐集提取关键概念: {len(note_paths)} 篇")
        all_extractions: List[str] = []

        for path in note_paths:
            stem = Path(path).stem
            extraction_path = extractions_dir / f"{stem}_extraction.md"

            # 检查是否已有提取结果（增量优化）
            if extraction_path.exists():
                self.logger.info(f"  {stem}: 使用已有提取结果")
                all_extractions.append(
                    f"### {stem}\n\n{extraction_path.read_text(encoding='utf-8')}"
                )
                continue

            try:
                content = read_file(path)
            except Exception as e:
                self.logger.warning(f"  {stem}: 读取失败 - {e}")
                continue

            extraction_prompt = build_extraction_prompt(stem, content)
            self.logger.info(f"  {stem}: 提取中...")
            try:
                extraction = provider.generate(
                    system_prompt, extraction_prompt,
                    max_tokens=2048, temperature=0.2
                )
                self._track_tokens(provider, "extraction")
                # COT 泄漏检测：提取结果混入思考过程/规划语言则带反馈重试一次
                leak = detect_cot_leak(extraction)
                if leak:
                    self.logger.warning(
                        f"  {stem}: 提取结果含思考过程/规划语言（{'、'.join(leak[:3])}），带反馈重试..."
                    )
                    corrected_prompt = extraction_prompt + (
                        "\n\n## 质量反馈\n\n上一次提取结果混入了思考过程/规划语言"
                        f"（{'、'.join(leak[:3])}）。请只输出最终提取内容本身，"
                        "不要输出任何思考过程、规划语言或对任务指令的复述。"
                    )
                    retry = provider.generate(
                        system_prompt, corrected_prompt,
                        max_tokens=2048, temperature=0.2
                    )
                    self._track_tokens(provider, "extraction_retry")
                    if not detect_cot_leak(retry):
                        extraction = retry
                extraction_path.write_text(extraction, encoding='utf-8')
                all_extractions.append(f"### {stem}\n\n{extraction}")
                self.logger.info(f"  {stem}: 提取完成 ({len(extraction)} chars)")
            except LLMError as e:
                self.logger.warning(f"  {stem}: 提取失败 - {e}")

        return all_extractions

    def _log_validation(self, synthesis_text: str, note_paths: List[str]) -> List[str]:
        """运行质量验证并记录问题，返回问题列表"""
        validation_issues = validate_synthesis(synthesis_text, note_paths)
        if validation_issues:
            self.logger.warning(f"合成质量检查发现 {len(validation_issues)} 个问题:")
            for issue in validation_issues:
                self.logger.warning(f"  - {issue}")
        return validation_issues

    def _validate_and_maybe_retry(
        self,
        provider: LLMProvider,
        system_prompt: str,
        prompt: str,
        synthesis_text: str,
        note_paths: List[str],
        purpose: str = "synthesis_merge",
        temperature: float = 0.3,
    ) -> Tuple[str, List[str]]:
        """运行合成质量验证；严重问题（COT 泄漏/缺章节/来源失配/文档重复）时带反馈重试，最多 2 次。

        Returns:
            (synthesis_text, validation_issues): 可能重试后的文本 + 最终验证问题列表
        """
        issues = self._log_validation(synthesis_text, note_paths)
        for attempt in range(1, 3):  # 最多重试 2 次（共 3 次尝试）
            critical = [i for i in issues if is_critical_issue(i)]
            if not critical:
                return synthesis_text, issues
            self.logger.warning(
                "合成质量验证未通过（%d 项严重问题），带反馈重试（第 %d 次）...",
                len(critical), attempt,
            )
            corrected_prompt = prompt + (
                "\n\n## 质量反馈\n\n上次输出的合成文档存在以下问题：\n- "
                + "\n- ".join(critical)
                + "\n请直接输出最终文档正文，不要输出任何思考过程、规划语言或对任务指令的复述。"
            )
            try:
                synthesis_text = provider.generate(
                    system_prompt, corrected_prompt,
                    max_tokens=16384, temperature=temperature,
                )
                self._track_tokens(provider, f"{purpose}_retry")
            except LLMError as e:
                self.logger.error(f"重试合成失败: {e}")
                return synthesis_text, issues
            issues = self._log_validation(synthesis_text, note_paths)
        return synthesis_text, issues

    def _save_synthesis(self, synthesis_text: str, domain_cfg: dict) -> str:
        """保存合成文档，返回文件路径"""
        output_name = domain_cfg.get('output_name', 'knowledge_synthesis')
        synthesis_path = str(self._notes_dir / f"{output_name}.md")
        write_file(synthesis_path, synthesis_text)
        self.logger.info(f"知识合成文档已保存: {synthesis_path}")
        return synthesis_path

    def _find_existing_synthesis(self, existing_path: Optional[str],
                                 domain_cfg: dict) -> Optional[str]:
        """按域查找匹配的合成文档路径"""
        if existing_path is not None:
            return existing_path

        output_name = domain_cfg.get('output_name', '')
        candidates = []
        if output_name:
            candidates.append(self._notes_dir / f"{output_name}.md")
        candidates.extend([
            self._notes_dir / "knowledge_synthesis.md",
            self._notes_dir / "短视频导演课程-知识体系.md",
            self._notes_dir / "短视频导演课程-知识体系_v5.md",
        ])
        for c in candidates:
            if c.exists():
                return str(c)
        return None
