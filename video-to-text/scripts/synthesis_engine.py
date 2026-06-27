# -*- coding: utf-8 -*-
"""
NoteForge 知识合成引擎
从 llm_note_engine.py 提取的合成相关方法，独立为可测试的模块。

职责：
  - 单次合成 (generate_synthesis)
  - 两阶段合成 (generate_synthesis_two_stage)
  - 增量更新 (update_synthesis_incremental)
  - 矛盾检测 (_detect_contradictions)
  - 合成 prompt 构建
  - 合成质量验证
"""

import os
import re
import logging
from pathlib import Path
from typing import List, Optional, Callable

from llm_providers import LLMProvider, LLMError


class SynthesisEngine:
    """知识合成引擎（独立于 LLMNoteEngine）"""

    def __init__(
        self,
        domain_classifier,
        notes_dir: Path,
        base_dir: Path,
        logger: logging.Logger,
        track_tokens_fn: Optional[Callable] = None,
    ):
        """
        Args:
            domain_classifier: DomainClassifier 实例，提供域检测/分组/校验
            notes_dir: 笔记输出目录
            base_dir: 项目根目录
            logger: 日志器
            track_tokens_fn: token 追踪回调，签名 (provider, purpose) -> None
        """
        self._domain_classifier = domain_classifier
        self._notes_dir = notes_dir
        self._base_dir = base_dir
        self.logger = logger
        self._track_tokens_fn = track_tokens_fn

    # ----------------------------------------------------------
    # 公开接口
    # ----------------------------------------------------------

    def generate_synthesis(
        self,
        note_paths: Optional[List[str]] = None,
        provider: Optional[LLMProvider] = None,
        prompt_builder=None,
        domain: Optional[str] = None,
    ) -> Optional[str]:
        """
        知识合成模式：读取多篇同域笔记，生成跨集知识框架
        自动按知识域隔离，只合成同域笔记，避免跨领域强行整合。

        Args:
            note_paths: 笔记文件路径列表（默认读取所有）
            provider: LLM 提供商实例
            prompt_builder: PromptBuilder 实例（当前未使用，保留扩展）
            domain: 指定知识域 ID（默认自动检测最大域）

        Returns:
            合成文档路径，或 None（失败）
        """
        if provider is None:
            self.logger.error("generate_synthesis: provider 不能为 None")
            return None

        # 收集笔记
        if note_paths is None:
            note_paths = sorted(str(p) for p in self._notes_dir.glob('*.md'))
            note_paths = [p for p in note_paths
                          if not Path(p).stem.startswith(('knowledge_',
                                                           'mental_models',
                                                           'action_playbook',
                                                           'extraction_',
                                                           'contradictions_'))]

        if not note_paths:
            self.logger.warning("未找到笔记文件")
            return None

        # 按知识域分组隔离
        groups = self._domain_classifier.get_notes_by_domain(note_paths)
        if len(groups) > 1:
            self.logger.info(f"检测到 {len(groups)} 个知识域，按域隔离合成:")
            for did, paths in groups.items():
                cfg = self._domain_classifier.get_domain_config(did)
                self.logger.info(f"  {cfg.get('name', did)}: {len(paths)} 篇")

        if domain:
            note_paths = groups.get(domain, note_paths)
        else:
            # 取笔记最多的域
            domain = max(groups.keys(), key=lambda k: len(groups[k])) if groups else 'general'
            note_paths = groups.get(domain, note_paths)

        domain_cfg = self._domain_classifier.get_domain_config(domain)
        self.logger.info(f"合成域: {domain_cfg.get('name', domain)} ({len(note_paths)} 篇)")

        self.logger.info(f"知识合成: {len(note_paths)} 篇笔记")

        # 读取所有笔记内容
        notes_content: List[str] = []
        for path in note_paths:
            try:
                content = self._read_file(path)
                stem = Path(path).stem
                notes_content.append(f"### {stem}\n\n{content}")
            except Exception as e:
                self.logger.warning(f"读取失败 {path}: {e}")

        if not notes_content:
            return None

        # 构建合成 prompt
        all_notes = "\n\n---\n\n".join(notes_content)

        synthesis_prompt = self._build_synthesis_prompt(all_notes)

        # 合成使用专用 system prompt（知识架构师视角）
        system_prompt = self._build_synthesis_system_prompt()

        self.logger.info(f"调用 LLM 生成知识合成文档...")
        try:
            synthesis_text = provider.generate(
                system_prompt, synthesis_prompt,
                max_tokens=16384  # 合成文档更长
            )
            self._track_tokens(provider, "synthesis")
        except LLMError as e:
            self.logger.error(f"合成生成失败: {e}")
            return None

        # 合成质量验证
        validation_issues = self._validate_synthesis(synthesis_text, note_paths)
        if validation_issues:
            self.logger.warning(f"合成质量检查发现 {len(validation_issues)} 个问题:")
            for issue in validation_issues:
                self.logger.warning(f"  - {issue}")

        # 保存合成文档（使用域专属文件名）
        output_name = domain_cfg.get('output_name', 'knowledge_synthesis')
        synthesis_path = str(self._notes_dir / f"{output_name}.md")
        self._write_file(synthesis_path, synthesis_text)
        self.logger.info(f"知识合成文档已保存: {synthesis_path}")

        return synthesis_path

    def generate_synthesis_two_stage(
        self,
        note_paths: Optional[List[str]] = None,
        provider: Optional[LLMProvider] = None,
        domain: Optional[str] = None,
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
        if provider is None:
            self.logger.error("generate_synthesis_two_stage: provider 不能为 None")
            return None

        # 收集笔记
        if note_paths is None:
            note_paths = sorted(str(p) for p in self._notes_dir.glob('*.md'))
            note_paths = [p for p in note_paths
                          if not Path(p).stem.startswith(('knowledge_',
                                                           'mental_models',
                                                           'action_playbook',
                                                           'extraction_',
                                                           'contradictions_'))]

        # 按知识域分组
        if not domain:
            groups = self._domain_classifier.get_notes_by_domain(note_paths)
            if len(groups) > 1:
                self.logger.info(f"检测到 {len(groups)} 个知识域，按域隔离合成:")
                for did, paths in groups.items():
                    cfg = self._domain_classifier.get_domain_config(did)
                    self.logger.info(f"  {cfg.get('name', did)}: {len(paths)} 篇")
            domain = max(groups.keys(), key=lambda k: len(groups[k])) if groups else 'general'
            note_paths = groups.get(domain, note_paths)
        else:
            note_paths = [p for p in note_paths if self._domain_classifier.detect_domain(p) == domain]

        domain_cfg = self._domain_classifier.get_domain_config(domain)
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
            else:
                self.logger.info(f"域 '{domain_cfg.get('name', domain)}' 有 {len(new_notes)} 篇新笔记，重新合成")

        system_prompt = self._build_synthesis_system_prompt()
        extractions_dir = self._notes_dir / "extractions"
        extractions_dir.mkdir(parents=True, exist_ok=True)

        # === Stage 1: 逐集提取 ===
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
                content = self._read_file(path)
            except Exception as e:
                self.logger.warning(f"  {stem}: 读取失败 - {e}")
                continue

            extraction_prompt = self._build_extraction_prompt(stem, content)

            self.logger.info(f"  {stem}: 提取中...")
            try:
                extraction = provider.generate(
                    system_prompt, extraction_prompt,
                    max_tokens=2048,
                    temperature=0.2
                )
                self._track_tokens(provider, "extraction")

                # 保存提取结果
                extraction_path.write_text(extraction, encoding='utf-8')
                all_extractions.append(f"### {stem}\n\n{extraction}")
                self.logger.info(f"  {stem}: 提取完成 ({len(extraction)} chars)")
            except LLMError as e:
                self.logger.warning(f"  {stem}: 提取失败 - {e}")

        if not all_extractions:
            self.logger.error("[Stage 1] 所有集数提取失败")
            return None

        # === Stage 2: 合并提炼 + 矛盾检测 ===
        self.logger.info(f"[Stage 2] 合并提炼: {len(all_extractions)} 份提取结果")

        # 合并所有提取结果
        merged_extractions = "\n\n---\n\n".join(all_extractions)

        # 矛盾检测
        contradictions = self._detect_contradictions(merged_extractions, provider)

        # 构建最终合成 prompt（含矛盾检测结果）
        merge_prompt = self._build_merge_prompt(merged_extractions, contradictions)

        self.logger.info("[Stage 2] 生成最终合成文档...")
        try:
            synthesis_text = provider.generate(
                system_prompt, merge_prompt,
                max_tokens=16384,
                temperature=0.3
            )
            self._track_tokens(provider, "synthesis_merge")
        except LLMError as e:
            self.logger.error(f"[Stage 2] 合成失败: {e}")
            return None

        # 质量验证
        validation_issues = self._validate_synthesis(synthesis_text, note_paths)
        if validation_issues:
            self.logger.warning(f"合成质量检查发现 {len(validation_issues)} 个问题:")
            for issue in validation_issues:
                self.logger.warning(f"  - {issue}")

        # 保存（域专属文件名）
        output_name = domain_cfg.get('output_name', 'knowledge_synthesis')
        synthesis_path = str(self._notes_dir / f"{output_name}.md")
        self._write_file(synthesis_path, synthesis_text)

        # 保存矛盾检测报告（域专属）
        if contradictions:
            contradictions_path = str(self._notes_dir / f"{output_name}_contradictions.md")
            self._write_file(contradictions_path, contradictions)
            self.logger.info(f"矛盾检测报告: {contradictions_path}")

        self.logger.info(f"合成文档已保存: {synthesis_path}")
        return synthesis_path

    def update_synthesis_incremental(
        self,
        new_note_path: str,
        provider: Optional[LLMProvider] = None,
        existing_synthesis_path: Optional[str] = None,
    ) -> Optional[str]:
        """
        增量更新知识合成文档（域隔离）：
        1. 检测新笔记的知识域
        2. 查找同域的合成文档
        3. 校验域匹配（不同域拒绝增量更新）
        4. 提取新笔记概念 → 更新同域文档

        Args:
            new_note_path: 新增笔记的路径
            provider: LLM 提供商实例
            existing_synthesis_path: 现有合成文档路径（默认按域自动查找）
        """
        if provider is None:
            self.logger.error("update_synthesis_incremental: provider 不能为 None")
            return None

        # 检测新笔记的知识域
        note_domain = self._domain_classifier.detect_domain(new_note_path)
        domain_cfg = self._domain_classifier.get_domain_config(note_domain)
        self.logger.info(f"新笔记域: {domain_cfg.get('name', note_domain)}")

        # 按域查找匹配的合成文档
        if existing_synthesis_path is None:
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
                    existing_synthesis_path = str(c)
                    break

        if not existing_synthesis_path or not Path(existing_synthesis_path).exists():
            self.logger.warning(f"未找到域 '{domain_cfg.get('name', note_domain)}' 的合成文档，将执行全量合成")
            return self.generate_synthesis_two_stage(
                note_paths=[new_note_path], provider=provider,
                domain=note_domain
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
                note_paths=[new_note_path], provider=provider,
                domain=note_domain
            )

        system_prompt = self._build_synthesis_system_prompt()

        # 读取新笔记和现有合成
        new_content = self._read_file(new_note_path)
        existing_synthesis = self._read_file(existing_synthesis_path)
        new_stem = Path(new_note_path).stem

        self.logger.info(f"增量更新: {new_stem} → {Path(existing_synthesis_path).name}")

        # 提取新笔记的关键概念
        extraction_prompt = self._build_extraction_prompt(new_stem, new_content)
        try:
            new_extraction = provider.generate(
                system_prompt, extraction_prompt,
                max_tokens=2048, temperature=0.2
            )
            self._track_tokens(provider, "incremental_extraction")
        except LLMError as e:
            self.logger.error(f"新笔记提取失败: {e}")
            return None

        # 增量更新 prompt
        update_prompt = (
            "请将新笔记的关键概念增量更新到现有知识合成文档中。\n\n"
            "## 更新规则\n\n"
            "1. **不重写全文** — 只更新受影响的章节\n"
            "2. **新增关联** — 如果新笔记与已有内容有关联，在关联图中新增\n"
            "3. **新增方法论** — 如果新笔记有新方法论，添加到对应章节\n"
            "4. **更新行动手册** — 如果新笔记有新行动建议，添加到对应场景\n"
            "5. **更新金句** — 在金句精选中新增新笔记的金句\n"
            "6. **检测矛盾** — 如果新笔记与已有内容矛盾，在「观点张力」章节标注\n"
            "7. **更新来源标注** — 确保新增内容标注了来源集数\n\n"
            "## 输出\n\n"
            "输出完整的更新后合成文档（不是增量 diff，而是完整文档）。\n\n"
            f"## 新增笔记关键概念（{new_stem}）\n\n{new_extraction}\n\n"
            f"## 现有合成文档\n\n{existing_synthesis}"
        )

        try:
            updated_synthesis = provider.generate(
                system_prompt, update_prompt,
                max_tokens=16384, temperature=0.3
            )
            self._track_tokens(provider, "incremental_update")
        except LLMError as e:
            self.logger.error(f"增量更新失败: {e}")
            return None

        # 验证
        all_notes = sorted(str(p) for p in self._notes_dir.glob('*.md'))
        all_notes = [p for p in all_notes
                     if not Path(p).stem.startswith(('knowledge_', 'mental_models',
                                                      'action_playbook', 'extraction_'))]
        validation_issues = self._validate_synthesis(updated_synthesis, all_notes)
        if validation_issues:
            self.logger.warning(f"增量更新质量检查: {len(validation_issues)} 个问题")
            for issue in validation_issues:
                self.logger.warning(f"  - {issue}")

        # 保存（使用域专用文件名）
        output_name = domain_cfg.get('output_name', 'knowledge_synthesis')
        synthesis_path = str(self._notes_dir / f"{output_name}.md")
        self._write_file(synthesis_path, updated_synthesis)
        self.logger.info(f"增量更新完成: {synthesis_path}")

        return synthesis_path

    # ----------------------------------------------------------
    # Prompt 构建
    # ----------------------------------------------------------

    def _build_synthesis_system_prompt(self) -> str:
        """合成专用 system prompt（知识架构师视角）"""
        return (
            "你是一位知识架构师，擅长从大量分散的学习笔记中发现模式、"
            "构建体系、提炼可迁移的知识框架。\n\n"
            "你的核心能力：\n"
            "1. **跨集关联发现** — 找出不同集数之间的知识关联和递进关系\n"
            "2. **主题重组** — 将按集组织的内容重组为按主题组织的知识体系\n"
            "3. **框架提炼** — 从具体案例中抽象出可迁移的方法论框架\n"
            "4. **忠实标注** — 每个知识点都标注来源，不添加原文不存在的内容\n\n"
            "你不做的事情：\n"
            "- 不逐集复述笔记内容\n"
            "- 不添加笔记中没有的观点或数据\n"
            "- 不将讲师的个人观点包装为客观规律\n"
            "- 不改写讲师的原话比喻"
        )

    def _build_synthesis_prompt(self, all_notes: str) -> str:
        """构建知识合成 prompt（v2.0：知识架构师视角 + 交叉验证）"""
        return (
            "请根据以下多篇学习笔记，生成一份跨集知识合成文档。\n\n"
            "## 你的角色\n\n"
            "你是一位知识架构师。你的任务不是简单汇总每篇笔记的摘要，"
            "而是从多篇笔记中发现**跨集的模式、关联和递进关系**，"
            "构建一个有机的知识体系。\n\n"
            "## 核心原则\n\n"
            "1. **提炼而非罗列** — 不要逐集摘要，要按主题重组\n"
            "2. **发现关联** — 找出不同集之间的知识关联（前置、互补、递进、对立）\n"
            "3. **标注来源** — 每个知识点标注「第X集」，确保引用准确\n"
            "4. **分层组织** — 思维模型 → 方法论框架 → 具体技巧 → 行动清单\n"
            "5. **保留原话** — 讲师的核心比喻和金句原样保留，不要改写\n\n"
            "## 忠实度约束\n\n"
            "- 每个知识点必须标注来源集数\n"
            "- 不得添加笔记中不存在的内容\n"
            "- 如果某集的内容在其他集中被引用/呼应，标注这种关联\n"
            "- 如果发现某集内容与其他集矛盾，如实标注\n\n"
            "## 输出结构\n\n"
            "```markdown\n"
            "# {课程名} · 系统化知识体系\n\n"
            "> {一句话定位}\n\n"
            "## 一、课程逻辑总览\n"
            "{用一段话描述课程的整体逻辑和进阶路径}\n\n"
            "## 二、核心思维模型\n"
            "### 2.1 {模型名}\n"
            "- **定义**: {一句话}\n"
            "- **来源**: 第X集\n"
            "- **关键要素**: {1. 2. 3.}\n"
            "- **关联模型**: 与 2.X {互补/递进/前置}\n"
            "- **金句**: > \"{讲师原话}\"\n\n"
            "## 三、方法论框架\n"
            "{按主题而非按集数组织，每个框架标注来源}\n\n"
            "## 四、跨集知识关联图\n"
            "| 知识点 A | 知识点 B | 关联类型 | 说明 |\n"
            "|----------|----------|----------|------|\n\n"
            "## 五、行动手册\n"
            "### 5.1 日常练习\n"
            "### 5.2 创作前\n"
            "### 5.3 创作中\n"
            "### 5.4 创作后\n\n"
            "## 六、学习路径\n"
            "### 阶段一 → 阶段二 → 阶段三 → 阶段四\n\n"
            "## 七、方法论速查表\n"
            "| 方法论 | 核心要点 | 来源集数 |\n\n"
            "## 八、金句精选\n"
            "{每集一句最有辨识度的原话}\n"
            "```\n\n"
            f"## 笔记内容\n\n{all_notes}"
        )

    def _build_extraction_prompt(self, episode_name: str, content: str) -> str:
        """构建单集概念提取 prompt"""
        return (
            f"请从以下笔记中提取关键概念，用于后续的跨集知识合成。\n\n"
            f"## 提取要求\n\n"
            f"从「{episode_name}」中提取：\n"
            f"1. **核心思维模型**（名称 + 一句话定义 + 关键要素）\n"
            f"2. **方法论/框架**（名称 + 步骤 + 适用场景）\n"
            f"3. **关键金句**（讲师原话，1-3 句最有辨识度的）\n"
            f"4. **与其他集可能的关联**（这个集的知识可能和哪些主题有关联？）\n"
            f"5. **核心关键词**（5-10 个，用于后续检索和关联）\n\n"
            f"## 格式\n\n"
            f"```markdown\n"
            f"### 核心模型\n"
            f"- {{{{模型名}}}}: {{{{定义}}}}\n\n"
            f"### 方法论\n"
            f"- {{{{方法名}}}}: {{{{步骤概要}}}}\n\n"
            f"### 金句\n"
            f"> \"{{{{原话}}}}\"\n\n"
            f"### 可能关联\n"
            f"- 与{{{{主题}}}}相关，因为...\n\n"
            f"### 关键词\n"
            f"{{{{词1, 词2, 词3, ...}}}}\n"
            f"```\n\n"
            f"## 笔记内容\n\n{content}"
        )

    def _build_merge_prompt(self, extractions: str,
                             contradictions: str = "") -> str:
        """构建合并提炼 prompt"""
        contradiction_section = ""
        if contradictions:
            contradiction_section = (
                "\n\n## 矛盾检测结果\n\n"
                "以下是从各集提取结果中发现的潜在矛盾或张力，"
                "请在合成文档中如实标注这些矛盾，并给出你的分析：\n\n"
                f"{contradictions}\n\n"
                "请在合成文档中新增「## 九、观点张力与矛盾」章节，"
                "如实呈现这些矛盾，不做裁决。"
            )

        return (
            "请根据以下逐集提取的关键概念，生成一份跨集知识合成文档。\n\n"
            "## 你的角色\n\n"
            "你是一位知识架构师。输入是每集的关键概念提取结果，"
            "你的任务是将它们重组为一个有机的知识体系。\n\n"
            "## 核心原则\n\n"
            "1. **提炼而非罗列** — 按主题重组，不按集数排列\n"
            "2. **发现关联** — 找出不同集之间的知识关联\n"
            "3. **标注来源** — 每个知识点标注「第X集」\n"
            "4. **保留原话** — 金句原样保留\n"
            "5. **如实呈现矛盾** — 如果不同集的观点有张力，如实标注\n"
            f"{contradiction_section}\n\n"
            "## 输出结构\n\n"
            "```markdown\n"
            "# {课程名} · 系统化知识体系\n\n"
            "## 一、课程逻辑总览\n"
            "## 二、核心思维模型\n"
            "## 三、方法论框架\n"
            "## 四、跨集知识关联图\n"
            "| 知识点 A | 知识点 B | 关联类型 | 说明 |\n"
            "## 五、行动手册\n"
            "### 5.1 日常练习 / 5.2 创作前 / 5.3 创作中 / 5.4 创作后\n"
            "## 六、学习路径\n"
            "## 七、方法论速查表\n"
            "## 八、金句精选\n"
            "## 九、观点张力与矛盾（如有）\n"
            "```\n\n"
            f"## 逐集概念提取结果\n\n{extractions}"
        )

    # ----------------------------------------------------------
    # 矛盾检测
    # ----------------------------------------------------------

    def _detect_contradictions(self, extractions: str,
                                provider: LLMProvider) -> str:
        """从各集提取结果中检测矛盾和张力"""
        self.logger.info("[矛盾检测] 分析各集提取结果中的潜在矛盾...")

        contradiction_prompt = (
            "请分析以下各集知识提取结果，找出其中的**矛盾、张力或对立观点**。\n\n"
            "## 检测维度\n\n"
            "1. **观点矛盾**: A 集说应该做 X，B 集说不应该做 X\n"
            "2. **方法冲突**: A 集推荐方法 M，B 集推荐方法 N，两者不兼容\n"
            "3. **优先级分歧**: A 集认为最重要的是 P，B 集认为最重要的是 Q\n"
            "4. **隐含张力**: 不是直接矛盾，但底层逻辑有张力（如\"先模仿\"vs\"要原创\"）\n\n"
            "## 输出格式\n\n"
            "如果没有发现矛盾，输出「未发现明显矛盾」。\n\n"
            "如果发现矛盾，按以下格式输出：\n"
            "```\n"
            "### 矛盾 1: {标题}\n"
            "- **A 方**: 第X集 — {观点}\n"
            "- **B 方**: 第Y集 — {观点}\n"
            "- **矛盾类型**: 观点矛盾 / 方法冲突 / 优先级分歧 / 隐含张力\n"
            "- **分析**: {这是真正的矛盾还是表面张力？两者是否可以调和？}\n"
            "```\n\n"
            f"## 各集提取结果\n\n{extractions}"
        )

        try:
            result = provider.generate(
                "你是一位批判性思维分析师，擅长发现不同观点之间的矛盾和张力。",
                contradiction_prompt,
                max_tokens=4096,
                temperature=0.2
            )
            self._track_tokens(provider, "contradiction_detection")

            # 检查是否真的发现了矛盾
            if "未发现明显矛盾" in result:
                self.logger.info("[矛盾检测] 未发现明显矛盾")
                return ""

            self.logger.info("[矛盾检测] 发现潜在矛盾，详见报告")
            return result
        except LLMError as e:
            self.logger.warning(f"[矛盾检测] 检测失败: {e}")
            return ""

    # ----------------------------------------------------------
    # 合成质量验证
    # ----------------------------------------------------------

    def _validate_synthesis(self, synthesis_text: str,
                             note_paths: List[str]) -> List[str]:
        """验证合成文档的质量"""
        issues = []

        # 1. 基本结构检查
        required_sections = ['思维模型', '方法论', '行动', '学习路径', '金句']
        for section in required_sections:
            if section not in synthesis_text:
                issues.append(f"缺少必要节: {section}")

        # 2. 来源标注检查 — 提取所有「第X集」引用，验证是否存在对应笔记
        ep_refs = re.findall(r'第(\d+)集', synthesis_text)
        if not ep_refs:
            issues.append("未找到任何「第X集」来源标注")
        else:
            # 检查引用的集数是否在笔记文件中存在
            available_eps = set()
            for p in note_paths:
                stem = Path(p).stem
                m = re.search(r'第(\d+)集', stem)
                if m:
                    available_eps.add(m.group(1))
            missing_eps = set(ep_refs) - available_eps
            if missing_eps:
                issues.append(f"引用了不存在的集数: {', '.join(sorted(missing_eps))}")

        # 3. 交叉关联检查 — 应有关联图或跨集引用
        cross_ref_patterns = [r'关联', r'前置', r'互补', r'递进', r'一脉相承', r'呼应']
        has_cross_ref = any(re.search(p, synthesis_text) for p in cross_ref_patterns)
        if not has_cross_ref:
            issues.append("缺少跨集知识关联（未发现关联/前置/互补等表述）")

        # 4. 信息密度检查 — 合成文档不应只是笔记的简单拼接
        lines = synthesis_text.split('\n')
        heading_count = sum(1 for l in lines if re.match(r'^#{1,3}\s', l))
        if heading_count < 10:
            issues.append(f"结构层次过少（仅{heading_count}个标题），可能是简单罗列")

        # 5. 金句检查
        quotes = [l for l in lines if l.strip().startswith('> "') or l.strip().startswith("> '")]
        if len(quotes) < 3:
            issues.append(f"金句过少（仅{len(quotes)}条），应保留讲师原话精华")

        return issues

    # ----------------------------------------------------------
    # 内部工具
    # ----------------------------------------------------------

    def _track_tokens(self, provider: LLMProvider, purpose: str = "generate"):
        """从 provider 读取 token 使用量并记录（通过回调）"""
        if self._track_tokens_fn is not None:
            self._track_tokens_fn(provider, purpose)

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
