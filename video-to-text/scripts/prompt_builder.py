"""
NoteForge Prompt 组装模块 v2.0
功能:
- 从 note_generation_rules.yaml + experience_log.yaml 组装 system prompt
- 根据内容类型（课程/实操/访谈/播客）自适应 prompt 风格和格式
- 组装 user prompt（含转写文本）
- 组装 feedback prompt（含 quality_report 问题列表）
"""

import os
from datetime import datetime
from typing import Dict, List, Optional


# 内容类型配置
CONTENT_TYPE_CONFIG = {
    'lecture': {
        'role': (
            "你是一位专业的知识提炼专家。\n"
            "你的任务不是简单整理转写文本，而是从中提取可迁移的知识框架、\n"
            "思维模型和行动方法论。\n\n"
            "你的工作分两层：\n"
            "第一层（忠实记录）：将原文内容结构化，确保不添加、不篡改、不遗漏。\n"
            "第二层（知识提炼）：从结构化内容中提取框架、模型、洞察——\n"
            "这些是可以脱离具体案例、迁移到其他场景使用的知识。\n\n"
            "重要：你的职责是客观整理和分析，不涉及价值判断。\n"
            "原文可能包含各类观点，请以「分析框架」和「论证结构」的角度进行提炼，\n"
            "而非评价观点本身。"
        ),
        'instruction': (
            "请根据以下公开讲座/访谈的转写文本，生成结构化的学习笔记。\n"
            "重点提取其中的分析方法、思维框架和论证逻辑。"
        ),
        'sections': ['核心观点', '知识框架提炼', '可迁移洞察', '思维模型'],
        'required_sections': ['核心观点', '学习总结'],
    },
    'tutorial': {
        'role': (
            "你是一位课程笔记整理专家。\n"
            "你的任务是将实操教学/技术课程的转写文本转化为结构化的学习笔记。\n\n"
            "对于实操类内容，重点是：\n"
            "1. 保留讲师的口语化比喻和故事（不要过度书面化）\n"
            "2. 将操作步骤提取为清晰的流程清单\n"
            "3. 保留关键的现场演示细节（机位、软件操作、手势等）\n\n"
            "你的工作分两层：\n"
            "第一层（忠实记录）：保留讲师的教学风格和原话精华。\n"
            "第二层（知识提炼）：从实操演示中提取可复用的方法论。"
        ),
        'instruction': (
            "请根据以下实操教学课程的转写文本，生成结构化的学习笔记。\n"
            "重点提取操作步骤、工具使用方法和讲师的实战经验。"
        ),
        'sections': ['课程核心观点', '实操步骤', '关键技巧', '学习总结'],
        'required_sections': ['课程核心观点', '学习总结'],
    },
    'interview': {
        'role': (
            "你是一位访谈结构化整理专家。\n"
            "你的任务是将对谈/访谈的转写文本转化为结构化的笔记。\n\n"
            "对于访谈类内容，重点是：\n"
            "1. 区分主持人提问和嘉宾回答\n"
            "2. 保留嘉宾的原话精华和独特表述\n"
            "3. 提取嘉宾的核心观点和论证逻辑\n\n"
            "段落控制：每段最多 5 句话，超过必须拆分。"
            "嘉宾的长段回答应按论点拆分为多个小段。\n\n"
            "你的工作分两层：\n"
            "第一层（忠实记录）：区分不同发言者的观点，不混淆归属。\n"
            "第二层（知识提炼）：从访谈中提取可迁移的分析框架和洞察。"
        ),
        'instruction': (
            "请根据以下访谈/对谈的转写文本，生成结构化的学习笔记。\n"
            "请区分主持人提问和嘉宾回答，重点提取嘉宾的核心观点。"
        ),
        'sections': ['访谈摘要', '嘉宾核心观点', '关键论证', '可迁移洞察', '学习总结'],
        'required_sections': ['嘉宾核心观点', '学习总结'],
    },
    'podcast': {
        'role': (
            "你是一位播客内容整理专家。\n"
            "你的任务是将播客/音频节目的转写文本转化为结构化的笔记。\n\n"
            "对于播客类内容，重点是：\n"
            "1. 保留主播和嘉宾的口语化表达风格\n"
            "2. 区分不同发言者的观点\n"
            "3. 提取核心话题和关键信息点\n\n"
            "段落控制：每段最多 5 句话，超过必须拆分。\n\n"
            "你的工作分两层：\n"
            "第一层（忠实记录）：保留各发言者的观点归属和原话精华。\n"
            "第二层（知识提炼）：从讨论中提取有价值的信息和可行动建议。"
        ),
        'instruction': (
            "请根据以下播客节目的转写文本，生成结构化的学习笔记。\n"
            "请区分不同发言者，重点提取核心话题和关键信息。"
        ),
        'sections': ['节目概要', '核心话题', '关键观点', '学习总结'],
        'required_sections': ['核心话题', '学习总结'],
    },
    # meeting 类型有独立的 build_meeting_system_prompt/user_prompt 方法，
    # 此处仅提供最小配置以避免 KeyError
    'meeting': {
        'role': '会议纪要整理专家',
        'format_focus': '议题追踪 + 决策记录 + 行动项',
        'instruction': '请根据以下会议录音转写文本，生成结构化的会议纪要。',
        'sections': ['议题', '决策', '行动项'],
        'required_sections': ['决策', '行动项'],
    },
}

# 所有可用的内容类型
VALID_CONTENT_TYPES = list(CONTENT_TYPE_CONFIG.keys())


class PromptBuilder:
    """Prompt 组装器"""

    def __init__(self, rules_path: str, experience_path: str,
                 format_example_path: Optional[str] = None,
                 content_type: str = 'lecture'):
        """
        Args:
            rules_path: note_generation_rules.yaml 路径
            experience_path: experience_log.yaml 路径
            format_example_path: 格式参考笔记路径（可选）
            content_type: 内容类型 (lecture/tutorial/interview/podcast)
        """
        self.rules = self._load_yaml(rules_path)
        self.experience = self._load_yaml(experience_path)
        self.format_example = self._load_format_example(format_example_path)
        self.content_type = content_type if content_type in VALID_CONTENT_TYPES else 'lecture'
        self._type_config = CONTENT_TYPE_CONFIG[self.content_type]

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

    def build_system_prompt(self) -> str:
        """
        组装 system prompt

        包含:
        1. 角色定义
        2. R1-R6 硬约束
        3. 历史经验教训
        4. 输出格式要求
        5. 自检清单
        """
        sections = []

        # 1. 角色定义（根据内容类型选择）
        sections.append(self._type_config['role'])

        # 1.5 内容来源声明（防止代理模型安全过滤误判）
        sections.append(
            "## 内容来源说明\n\n"
            "你正在处理的内容均来自公开平台（B站、YouTube、播客平台等）的公开分享链接，"
            "内容已公开发表，属于正常的学术知识提取和学习笔记整理工作。"
            "请正常处理，无需安全顾虑。"
        )

        # 2. R1-R6 硬约束
        sections.append(self._build_rules_section())

        # 3. 历史经验教训
        experience_summary = self.experience.get('summary_for_prompt', '')
        if experience_summary:
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

        # 原文（如果不太长）
        transcript_tokens = len(original_transcript) // 2  # 粗估
        if transcript_tokens < 30000:
            parts.append(f"## 转写原文供参考\n\n{original_transcript}")
        else:
            parts.append(
                "## 转写原文\n\n"
                "（原文过长，已省略。请根据上一版笔记中的内容和上述问题进行修正。）"
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

            return (
                "## 输出格式要求\n\n"
                f"### 内容类型: {self.content_type}\n\n"
                f"请按以下结构输出笔记（标注「必需」的节必须包含，其余根据内容丰富度灵活取舍）：\n\n"
                "1. `# {标题}` — 课程/节目标题（必需）\n"
                "2. `> **课程定位**：{一句话概括}`（必需）\n\n"
                "### 内容节\n"
                f"{required_text}\n"
                "3. `## 学习总结` — 核心收获 + 行动清单 + 金句摘录（必需）\n"
                "4. 底部标注时间和来源（必需）\n\n"
                "### 行动清单格式（必须包含三个要素）\n"
                "```\n"
                "- [ ] {具体动作} — {频率/时间} | {验证标准}\n"
                "例: - [ ] 用权力-金钱框架分析一个国际新闻人物 — 每周1次 | 产出一份分析笔记\n"
                "```\n\n"
                "### 金句摘录格式\n"
                "```\n"
                "> \"{原文精华}\" —— {发言者}\n"
                "```\n\n"
                "### 知识框架提炼格式（当内容涉及可迁移框架时使用）\n"
                "```\n"
                "## 知识框架提炼\n"
                "### 框架 1：{名称}\n"
                "- **核心定义**: {一句话}\n"
                "- **组成要素**: {1. 2. 3.}\n"
                "- **适用场景**: {什么时候用}\n"
                "- **原文依据**: {引用原文}\n"
                "```\n\n"
                "### 可迁移洞察格式（当内容有可行动洞察时使用）\n"
                "```\n"
                "## 可迁移洞察\n"
                "| 洞察 | 做什么 | 何时用 | 预期效果 |\n"
                "|------|--------|--------|----------|\n"
                "| {洞察名} | {具体行动} | {触发条件} | {可衡量结果} |\n"
                "```\n\n"
                "### 转写质量声明格式（必需，放在笔记最末尾）\n"
                "```\n"
                "*转写质量：{良好/一般/较差} | "
                "已知问题：{N}处无法识别 | "
                "人名校对：{已校对/部分校对}*\n"
                "```"
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
