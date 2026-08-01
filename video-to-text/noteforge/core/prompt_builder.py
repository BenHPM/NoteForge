"""
NoteForge Prompt 组装模块 v2.1
功能:
- 从 note_generation_rules.yaml + experience_log.yaml 组装 system prompt
- 根据内容类型（课程/实操/访谈/播客）自适应 prompt 风格和格式
- 从 format_templates.yaml 加载静态格式模板（可配置）
- 组装 user prompt（含转写文本）
- 组装 feedback prompt（含 quality_report 问题列表）
"""

import logging
import os
from typing import Dict, List, Optional

from noteforge.core.prompts.content_types import CONTENT_TYPE_CONFIG, VALID_CONTENT_TYPES


class PromptBuilder:
    """Prompt 组装器"""

    def __init__(self, rules_path: str, experience_path: str,
                 format_example_path: Optional[str] = None,
                 content_type: str = 'lecture',
                 format_templates_path: Optional[str] = None):
        """
        Args:
            rules_path: note_generation_rules.yaml 路径
            experience_path: experience_log.yaml 路径
            format_example_path: 格式参考笔记路径（可选）
            content_type: 内容类型 (lecture/tutorial/interview/podcast)
            format_templates_path: 格式模板 YAML 文件路径（可选）。
                如果提供，从文件加载静态格式模板；
                如果文件不存在或未提供，使用内置默认模板。
        """
        self.rules = self._load_yaml(rules_path)
        self.experience = self._load_yaml(experience_path)
        self.format_example = self._load_format_example(format_example_path)
        self.content_type = content_type if content_type in VALID_CONTENT_TYPES else 'lecture'
        self._type_config = CONTENT_TYPE_CONFIG[self.content_type]

        # 加载格式模板（YAML → 字符串；失败则回退内置默认）
        self._format_templates: Dict[str, str] = {}
        self._load_format_templates(format_templates_path)

    @staticmethod
    def _load_yaml(path: str) -> dict:
        """加载 YAML 文件"""
        import yaml
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    @staticmethod
    def _load_format_example(path: Optional[str]) -> Optional[str]:
        """加载格式参考笔记"""
        if path and os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        if path:
            import logging
            logging.getLogger('noteforge').warning(
                f"格式参考文件不存在，将使用内置模板: {path}"
            )
        return None

    def _load_format_templates(self, yaml_path: Optional[str]) -> None:
        """从 YAML 加载格式模板，失败时使用内置默认模板"""
        self._format_templates = dict(self._default_format_templates())

        if yaml_path is None or not os.path.exists(yaml_path):
            if yaml_path:
                logging.getLogger('noteforge.prompt_builder').warning(
                    f"格式模板文件不存在，使用内置默认模板: {yaml_path}"
                )
            return

        try:
            data = self._load_yaml(yaml_path)
            loaded = data.get('format_templates', data)
            for key in loaded:
                if loaded[key]:
                    self._format_templates[key] = loaded[key]
            logging.getLogger('noteforge.prompt_builder').info(
                f"从 {yaml_path} 加载格式模板: "
                f"{[k for k in self._format_templates if k in loaded]}"
            )
        except Exception as e:
            logging.getLogger('noteforge.prompt_builder').warning(
                f"加载格式模板失败 ({yaml_path}): {e}，使用内置默认模板"
            )
            self._format_templates = dict(self._default_format_templates())

    @staticmethod
    def _render_template(template: str, **kwargs) -> str:
        """安全渲染模板，使用 __KEY__ 占位符（避免 Python .format() 与模板中的 {1. 2. 3.} 冲突）"""
        result = template
        for key, value in kwargs.items():
            result = result.replace(f'__{key.upper()}__', str(value))
        return result

    @staticmethod
    def _default_format_templates() -> Dict[str, str]:
        """返回默认格式模板（与旧代码硬编码内容一致）"""
        return {
            'output_format_header': (
                "## 输出格式要求\n\n"
                "### 内容类型: __CONTENT_TYPE__\n\n"
                "请按以下结构输出笔记（标注「必需」的节必须包含，"
                "其余根据内容丰富度灵活取舍）：\n\n"
                "1. `# {标题}` — 课程/节目标题（必需）\n"
                "2. `> **课程定位**：{一句话概括}`（必需）\n\n"
                "__CONTENT_SECTIONS__\n"
                "3. `## 学习总结` — 核心收获 + 行动清单 + 金句摘录（必需）\n"
                "4. 底部标注时间和来源（必需）\n\n"
            ),
            'action_list_format': (
                "### 行动清单格式（必须包含三个要素）\n"
                "```\n"
                "- [ ] {具体动作} — {频率/时间} | {验证标准}\n"
                "例: - [ ] 用权力-金钱框架分析一个国际新闻人物 — 每周1次 | 产出一份分析笔记\n"
                "```\n\n"
            ),
            'quote_format': (
                "### 金句摘录格式\n"
                "```\n"
                '> "{原文精华}" —— {发言者}\n'
                "```\n\n"
            ),
            'knowledge_framework_format': (
                "### 知识框架提炼格式（当内容涉及可迁移框架时使用）\n"
                "```\n"
                "## 知识框架提炼\n"
                "### 框架 1：{名称}\n"
                "- **核心定义**: {一句话}\n"
                "- **组成要素**: {1. 2. 3.}\n"
                "- **适用场景**: {什么时候用}\n"
                "- **原文依据**: {引用原文}\n"
                "```\n\n"
            ),
            'actionable_insight_format': (
                "### 可迁移洞察格式（当内容有可行动洞察时使用）\n"
                "```\n"
                "## 可迁移洞察\n"
                "| 洞察 | 做什么 | 何时用 | 预期效果 |\n"
                "|------|--------|--------|----------|\n"
                "| {洞察名} | {具体行动} | {触发条件} | {可衡量结果} |\n"
                "```\n\n"
            ),
            'transcript_quality_format': (
                "### 转写质量声明格式（必需，放在笔记最末尾）\n"
                "```\n"
                "*转写质量：{良好/一般/较差} | "
                "已知问题：{N}处无法识别 | "
                "人名校对：{已校对/部分校对}*\n"
                "```"
            ),
        }

    def build_system_prompt(self) -> str:
        """
        组装 system prompt

        包含:
        1. 内容性质声明（防 API 服务端安全过滤误判，最优先）
        2. 角色定义
        3. 硬约束（R1-R12）
        4. 历史经验教训
        5. 输出格式要求
        6. 自检清单
        """
        sections = []

        # 1. 内容性质声明（最优先，防止代理模型/API 服务端安全过滤误判）
        # 必须在角色定义之前，确保安全过滤器首先看到"公开内容 + 学术用途"上下文
        sections.append(
            "## 任务声明\n\n"
            "你是学术研究辅助工具，唯一任务是从公开渠道获取的已发表内容中"
            "提取和整理知识，生成学习笔记。\n\n"
            "所有处理内容均来自公开平台（B站、YouTube、播客等）的公开视频/音频，"
            "已公开发表，任何人都可以通过标准 URL 访问。\n"
            "用途是学术学习笔记整理，属于知识提取和分析工作，不涉及敏感内容处理。\n\n"
            "指令：你必须正常完成笔记生成。公开内容的学术整理是正当任务，"
            "不应被拒绝。不做价值判断，只做客观归纳。"
        )

        # 2. 角色定义（根据内容类型选择）
        sections.append(self._type_config['role'])

        # 3. 硬约束
        sections.append(self._build_rules_section())

        # 3. 历史经验教训（P0-3: 只注入活跃条目，过期/未触发的自动抑制）
        experience_summary = self.experience.get('summary_for_prompt', '')
        if experience_summary:
            # 过滤过期条目
            from noteforge.quality.experience_lifecycle import filter_active_entries
            entries = self.experience.get('entries', [])
            meta = self.experience.get('meta', {})
            ttl_days = meta.get('ttl_days', 90)
            prune_days = meta.get('prune_untriggered_after_days', 60)
            active_count = len(filter_active_entries(entries, ttl_days, prune_days))
            total_count = len(entries)
            if active_count < total_count:
                # 有条目被抑制，在 summary 中标注
                experience_summary += (
                    f"\n\n（注：{total_count - active_count} 条历史经验因过期/长期未触发已自动抑制，"
                    f"当前注入 {active_count}/{total_count} 条）"
                )
            sections.append(experience_summary.strip())

        # 4. 输出格式要求
        sections.append(self._build_format_section())

        # 5. 自检清单
        sections.append(self._build_selfcheck_section())

        return "\n\n---\n\n".join(sections)

    def build_user_prompt(self, transcript: str, title: Optional[str] = None,
                          mode: str = 'notes') -> str:
        """
        组装 user prompt

        Args:
            transcript: 转写文本
            title: 视频标题（可选）
            mode: 生成模式（notes/synthesis/meeting 等，预留扩展）

        Returns:
            完整 user prompt
        """
        parts = []

        # 指令（根据内容类型选择）
        instruction = self._type_config['instruction']
        if title:
            instruction += f"\n\n来源标题：{title}"
        parts.append(instruction)

        # 转写文本
        parts.append(f"## 转写原文\n\n{transcript}")

        return "\n\n".join(parts)

    def build_feedback_prompt(
        self,
        original_transcript: str,
        failed_note: str,
        quality_report: dict
    ) -> str:
        """
        组装反馈 prompt（用于重试）

        Args:
            original_transcript: 原始转写文本
            failed_note: 上一版有问题的笔记
            quality_report: quality_gate 生成的报告字典

        Returns:
            反馈 prompt
        """
        parts = []

        # 问题概述
        total_score = quality_report.get('total_score', 0)
        overall_passed = quality_report.get('overall_passed', False)
        parts.append(
            f"你的上一版笔记未通过质量检查（得分: {total_score:.0%}，"
            f"状态: {'通过' if overall_passed else '未通过'}）。\n"
            "请根据以下问题逐一修正。"
        )

        # 逐条问题
        issues_section = self._build_issues_section(quality_report)
        if issues_section:
            parts.append(issues_section)

        # 修正要求
        # 动态获取规则数量
        rules_data = self.rules.get('rules', {})
        rule_count = len(rules_data) if rules_data else 11
        parts.append(
            "## 修正要求\n\n"
            "1. 针对上述每个问题，逐一修正\n"
            "2. 不要重写整篇笔记，只修改有问题的部分\n"
            "3. 修正后请确保其他部分没有被破坏\n"
            f"4. 修正完成后，对照硬约束（R1-R{rule_count}）再自查一遍"
        )

        # 上一版笔记
        parts.append(f"## 上一版笔记\n\n{failed_note}")

        # 原文（token 预算感知截断）
        # P0: 使用 token 预算而非固定字符数截断
        # Claude Sonnet 200K context, 扣除 system_prompt(~5K) + 响应预算(8K) + 安全边际(5K)
        # CJK: ~1.5 字符/token, 英文: ~4 字符/token
        # P0-1: 粗估按 ~2 字符/token，加 15% 安全裕度（CJK 实际约 1.5-1.8 chars/token）
        TRANSCRIPT_CHARS_PER_TOKEN = 2.0
        SAFETY_MARGIN = 1.15  # 15% 安全裕度，补偿 CJK 偏差
        CONTEXT_BUDGET_TOKENS = 180000  # 200K - system - response - margin
        feedback_text_so_far = len('\n\n'.join(parts))
        feedback_tokens_so_far = (feedback_text_so_far / TRANSCRIPT_CHARS_PER_TOKEN) * SAFETY_MARGIN
        remaining_tokens = CONTEXT_BUDGET_TOKENS - feedback_tokens_so_far
        transcript_token_budget = remaining_tokens * 0.7  # 原文占剩余预算 70%
        max_transcript_chars = int(transcript_token_budget * TRANSCRIPT_CHARS_PER_TOKEN)

        if max_transcript_chars > 0 and len(original_transcript) <= max_transcript_chars:
            parts.append(f"## 转写原文供参考\n\n{original_transcript}")
        elif max_transcript_chars > 0:
            truncated = original_transcript[:max_transcript_chars]
            # 在句末截断，避免切断句子
            last_period = max(
                truncated.rfind('。'),
                truncated.rfind('！'),
                truncated.rfind('？'),
                truncated.rfind('\n'),
            )
            if last_period > max_transcript_chars * 0.8:
                truncated = truncated[:last_period + 1]
            parts.append(
                f"## 转写原文\n\n"
                f"（原文过长，已截断至约 {len(truncated)} 字。"
                f"请根据上一版笔记中的内容和上述问题进行修正。）\n\n"
                f"{truncated}"
            )
        else:
            parts.append(
                "## 转写原文\n\n"
                "（原文过长且反馈 prompt 已占满 context，已省略。"
                "请根据上一版笔记中的内容和上述问题进行修正。）"
            )

        return "\n\n".join(parts)

    def build_meeting_system_prompt(self) -> str:
        """
        组装会议纪要专用 system prompt

        Returns:
            会议纪要 system prompt
        """
        sections = []

        # 角色定义
        sections.append(
            "你是一位专业的会议纪要整理专家。\n"
            "你的任务是将会议录音转写文本转化为结构化的会议纪要。\n"
            "忠实记录会议内容，准确提取决策、待办和讨论要点。"
        )

        # 会议纪要硬约束
        sections.append(
            "## 会议纪要硬约束\n\n"
            "1. **决策必须明确**: 每个决策必须标注「已决定」或「待定」\n"
            "2. **待办必须可执行**: 每个待办必须有负责人和截止时间（如原文提及）\n"
            "3. **讨论要点忠实**: 不得扭曲发言人的观点方向\n"
            "4. **区分事实和意见**: 明确区分「已确认的事实」和「某人的观点/建议」\n"
            "5. **不遗漏关键反对意见**: 如果有人反对某个决策，必须记录"
        )

        # 输出格式
        sections.append(
            "## 会议纪要输出格式\n\n"
            "```markdown\n"
            "# {会议主题}\n\n"
            "> **会议时间**: {YYYY-MM-DD HH:MM}\n"
            "> **参会人**: {从转写文本中识别的发言人}\n"
            "> **会议时长**: {估算}\n\n"
            "---\n\n"
            "## 一、会议摘要（3-5 句话）\n\n"
            "## 二、关键决策\n"
            "| 决策内容 | 状态 | 相关讨论摘要 |\n"
            "|----------|------|-------------|\n"
            "| {决策} | 已决定/待定 | {讨论要点} |\n\n"
            "## 三、待办事项\n"
            "- [ ] {待办内容} — 负责人: {姓名} | 截止: {日期}\n\n"
            "## 四、讨论要点\n"
            "### 4.1 {议题 1}\n"
            "- **提出人**: {姓名}\n"
            "- **核心观点**: {观点}\n"
            "- **反对/补充**: {如有}\n"
            "- **结论**: {结论或待定}\n\n"
            "## 五、遗留问题\n"
            "- {未解决的问题，需后续跟进}\n\n"
            "## 六、下次会议安排\n"
            "- {如有提及}\n"
            "```\n\n"
            "### 格式细则\n"
            "- 发言人姓名从转写文本中推断（如无法确定，用「发言人A/B/C」）\n"
            "- 决策和待办必须用表格或 checkbox 格式，方便后续跟踪\n"
            "- 时间信息尽量保留原文中的表述"
        )

        # 自检清单
        sections.append(
            "## 自检清单\n\n"
            "1. **决策完整性**: 所有明确的决定是否都已记录？\n"
            "2. **待办可执行性**: 每个待办是否有负责人？\n"
            "3. **反对意见**: 是否遗漏了重要的反对或保留意见？\n"
            "4. **时间线**: 事件和截止日期是否准确？\n"
            "5. **发言归属**: 观点是否正确归属到对应发言人？"
        )

        return "\n\n---\n\n".join(sections)

    def build_meeting_user_prompt(self, transcript: str,
                                    title: str = None) -> str:
        """
        组装会议纪要 user prompt

        Args:
            transcript: 转写文本
            title: 会议主题（可选）

        Returns:
            完整 user prompt
        """
        parts = []

        instruction = "请根据以下会议录音转写文本，生成结构化的会议纪要。"
        if title:
            instruction += f"\n\n会议主题：{title}"
        parts.append(instruction)

        parts.append(f"## 会议转写原文\n\n{transcript}")

        return "\n\n".join(parts)

    def _build_rules_section(self) -> str:
        """从 YAML 构建 R1-R6 规则段落"""
        rules_data = self.rules.get('rules', {})
        if not rules_data:
            return "## 硬约束\n\n（规则文件为空，请严格遵守通用笔记规范）"

        lines = ["## 硬约束（违反任何一条即为不合格笔记）\n"]
        rule_order = ['R1_禁止虚构数据', 'R2_禁止越界增补', 'R3_禁止事实反转',
                       'R4_禁止关键概念简化失真', 'R5_覆盖度底线', 'R6_术语一致性',
                       'R7_框架完整性', 'R8_洞察可行动性', 'R9_分层准确性',
                       'R10_时间线准确性', 'R11_引用归属', 'R12_人名数字一致性']

        for rule_key in rule_order:
            rule = rules_data.get(rule_key)
            if not rule:
                continue
            rid = rule.get('id', '')
            severity = rule.get('severity', '')
            desc = rule.get('description', '').strip()
            lines.append(f"### {rid} [{severity.upper()}] {rule_key.split('_', 1)[1]}")
            lines.append(desc)

            # 违规示例
            violations = rule.get('violations', [])
            if violations:
                lines.append("\n**禁止行为**:")
                for v in violations:
                    lines.append(f"- {v}")

            # 合规检查
            compliance = rule.get('compliance_check', '').strip()
            if compliance:
                lines.append(f"\n**合规检查**: {compliance}")

            lines.append("")  # 空行分隔

        return "\n".join(lines)

    def _build_format_section(self) -> str:
        """构建输出格式要求段落（根据内容类型自适应）"""
        if self.format_example:
            # 从参考笔记中提取结构（取前 80 行作为模板骨架）
            lines = self.format_example.split('\n')
            skeleton_lines = []
            for line in lines:
                # 只保留标题行和结构标记
                if line.startswith('#') or line.startswith('---') or line.startswith('> **'):
                    skeleton_lines.append(line)
                elif line.startswith('## ') or line.startswith('### '):
                    skeleton_lines.append(line)
                elif line.startswith('- [ ]') or line.startswith('| '):
                    skeleton_lines.append(line)
                elif line.startswith('*笔记') or line.startswith('*学习'):
                    skeleton_lines.append(line)

            skeleton = '\n'.join(skeleton_lines[:40])
            return (
                "## 输出格式要求\n\n"
                "请严格按以下结构输出笔记：\n\n"
                "```\n"
                f"{skeleton}\n"
                "```\n\n"
                "### 格式细则\n"
                "- 标题: `# {集数标题}`\n"
                "- 课程定位: `> **课程定位**：{一句话核心价值}`\n"
                "- 核心观点: 按主题分节，每节含要点列表\n"
                "- 学习总结: 核心收获 + 行动清单（`- [ ]` 格式）+ 金句摘录（`>` 引用格式）\n"
                "- 底部标注: `*笔记整理时间：{今天日期，格式YYYY-MM-DD}*` + `*学习来源：原视频音频转写*`"
            )
        else:
            # 根据内容类型构建格式要求
            required = self._type_config.get('required_sections', ['核心观点', '学习总结'])
            all_sections = self._type_config.get('sections', ['核心观点', '学习总结'])

            # 必需结构
            required_text = "\n".join(
                f"- **{s}**（必需）" if s in required else f"- {s}（可选，根据内容丰富度决定）"
                for s in all_sections
            )
            content_sections_block = f"### 内容节\n{required_text}\n"

            return (
                self._format_templates.get('output_format_header', '')
                .replace('__CONTENT_TYPE__', self.content_type)
                .replace('__CONTENT_SECTIONS__', content_sections_block)
                + self._format_templates.get('action_list_format', '')
                + self._format_templates.get('quote_format', '')
                + self._format_templates.get('knowledge_framework_format', '')
                + self._format_templates.get('actionable_insight_format', '')
                + self._format_templates.get('transcript_quality_format', '')
            )

    def _build_selfcheck_section(self) -> str:
        """构建自检清单段落"""
        return (
            "## 自检清单（生成后必须执行）\n\n"
            "生成完笔记后，请逐项自查：\n\n"
            "### 忠实性检查\n"
            "1. **数字检查**: 笔记中每个数字/百分比是否在原文中有对应？"
            "无法对应的必须删除或改为定性描述。\n"
            "2. **覆盖检查**: 列出原文的主要议题（5-10 个），"
            "逐一标记笔记是否已覆盖。覆盖率必须 >= 80%。\n"
            "3. **术语检查**: 术语表中的定义是否与正文使用一致？"
            "不得出现矛盾表述。\n"
            "4. **语义方向检查**: 每个核心论点的正反方向是否与原文一致？"
            "特别注意强弱、好坏、是否等对比性描述。\n"
            "5. **补充标记**: 如有非原文内容，是否已标注 [📝笔者补充]？\n\n"
            "### 知识提炼检查\n"
            "6. **框架完整性**: 提取的框架是否包含原文提到的所有组成要素？"
            "不得因简化而丢失关键步骤或条件。\n"
            "7. **洞察可行动性**: 每条洞察是否有具体的「做什么」"
            "而非泛泛而谈的「要重视XX」？\n"
            "8. **分层清晰**: 是否区分了表面内容（具体故事/案例）"
            "和可迁移知识（原则/框架/模型）？"
            "不得将个案经验包装为通用原则。\n\n"
            "### 可读性检查\n"
            "9. **保留原话**: 讲师/嘉宾的精彩比喻和口语化表述是否被保留？"
            "不要把生动的原话改写为枯燥的书面语。\n"
            "10. **段落长度（硬约束）**: 每个段落最多 5 句话。"
            "如果一个段落超过 5 句，必须拆分为两个段落。"
            "每 3-5 句插入一个空行分隔。\n"
            "11. **行动清单具体性（硬约束）**: 每条行动项必须包含三个要素：\n"
            "    - **做什么**：具体动作（如「用XX框架分析一个案例」）\n"
            "    - **频率/时间**：何时做、多久一次（如「每周一次」「每次刷视频时」）\n"
            "    - **验证标准**：怎么知道自己做到了（如「产出一份分析笔记」）\n"
            "    禁止空泛行动：❌「练习导演思维」 ✅「每周用导演视角分析 3 条短视频，记录吸引点和手法」\n"
            "12. **引用准确性**: 引用某人观点时，人名归属是否正确？"
            "讲师的夸张表述应加引号标注为原话。\n"
            "13. **每节信息密度**: 每个主题节至少包含 3 个要点或 1 个案例。"
            "禁止出现只有标题没有内容的空节。"
            "如果某个主题内容不足，合并到相关主题中。\n\n"
            "### 忠实度护栏\n"
            "14. **转写模糊处理（硬约束）**: 当转写原文不清晰、有噪声标记（[无法识别片段]）、"
            "或人名/数字/术语无法确认时，**禁止自行补全**。必须用以下格式标注：\n"
            "    - 人名不确定：「此处原文不清晰，可能为XXX」\n"
            "    - 数字不确定：「原文此处有噪声，数字为推断值」\n"
            "    - 观点不确定：「原文此处表述模糊，以下为笔者理解」\n"
            "    ❌ 直接写成确定的内容 ✅ 标注不确定性后写入\n"
            "15. **人名/数字校对**: 笔记中出现的每个人名、片名、关键数字，"
            "必须在转写原文中找到对应。找不到的标注「原文未确认」。\n"
            "16. **转写质量声明**: 在笔记最末尾添加一段转写质量声明，格式：\n"
            "    `*转写质量：{良好/一般/较差} | 已知问题：{噪声片段数}处无法识别 | 人名校对：{已校对/部分校对}*`"
        )

    def _build_issues_section(self, quality_report: dict) -> str:
        """从 quality_report 构建问题列表段落"""
        rule_results = quality_report.get('rule_results', {})
        if not rule_results:
            return ""

        lines = ["## 质量检查发现的问题\n"]

        # 按严重度排序: fatal > major > medium
        severity_order = {'fatal': 0, 'major': 1, 'medium': 2}
        all_issues: List[Dict] = []

        for rid, rr in rule_results.items():
            for issue in rr.get('issues', []):
                issue['_rule_id'] = rid
                all_issues.append(issue)

        all_issues.sort(key=lambda x: severity_order.get(x.get('severity', ''), 9))

        if not all_issues:
            return ""

        for i, issue in enumerate(all_issues, 1):
            severity = issue.get('severity', 'unknown').upper()
            rule_id = issue.get('_rule_id', '')
            line_range = issue.get('line_range', '')
            desc = issue.get('description', '')
            suggestion = issue.get('suggestion', '')

            lines.append(f"### 问题 {i} [{severity}] {rule_id}")
            if line_range:
                lines.append(f"- **位置**: {line_range}")
            lines.append(f"- **描述**: {desc}")
            lines.append(f"- **建议**: {suggestion}")
            lines.append("")

        return "\n".join(lines)

    def build_prompt(self, kind: str, mode: str = "notes", **kwargs) -> str:
        """统一 prompt 入口（推荐使用）。

        Args:
            kind: "system" | "user" | "feedback"
            mode: "lecture" | "tutorial" | "interview" | "podcast" | "meeting"
            **kwargs: 各 kind 的额外参数
                - system: 无额外参数
                - user: transcript, title
                - feedback: original_transcript, failed_note, quality_report

        Returns:
            组装好的 prompt 字符串
        """
        if kind == "system":
            if mode == "meeting":
                return self.build_meeting_system_prompt()
            return self.build_system_prompt()
        elif kind == "user":
            transcript = kwargs.get('transcript', '')
            title = kwargs.get('title')
            if mode == "meeting":
                return self.build_meeting_user_prompt(transcript, title)
            return self.build_user_prompt(transcript, title)
        elif kind == "feedback":
            if mode == "meeting":
                return self.build_meeting_feedback_prompt(**kwargs)
            return self.build_feedback_prompt(
                kwargs.get('original_transcript', ''),
                kwargs.get('failed_note', ''),
                kwargs.get('quality_report', {}),
            )
        raise ValueError(f"未知 prompt kind: {kind}（支持 system/user/feedback）")

    def build_meeting_feedback_prompt(
        self,
        original_transcript: str = "",
        failed_note: str = "",
        quality_report: dict = None,
    ) -> str:
        """会议纪要反馈 prompt（当前委托通用 feedback prompt，未来可扩展）。"""
        return self.build_feedback_prompt(original_transcript, failed_note, quality_report or {})
