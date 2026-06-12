"""
NoteForge Prompt 组装模块 v1.0
功能:
- 从 note_generation_rules.yaml + experience_log.yaml 组装 system prompt
- 组装 user prompt（含转写文本）
- 组装 feedback prompt（含 quality_report 问题列表）
"""

import os
from datetime import datetime
from typing import Dict, List, Optional


class PromptBuilder:
    """Prompt 组装器"""

    def __init__(self, rules_path: str, experience_path: str,
                 format_example_path: Optional[str] = None):
        """
        Args:
            rules_path: note_generation_rules.yaml 路径
            experience_path: experience_log.yaml 路径
            format_example_path: 格式参考笔记路径（可选）
        """
        self.rules = self._load_yaml(rules_path)
        self.experience = self._load_yaml(experience_path)
        self.format_example = self._load_format_example(format_example_path)

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

        # 1. 角色定义（知识提炼专家）
        sections.append(
            "你是一位专业的知识提炼专家。\n"
            "你的任务不是简单整理转写文本，而是从中提取可迁移的知识框架、\n"
            "思维模型和行动方法论。\n\n"
            "你的工作分两层：\n"
            "第一层（忠实记录）：将原文内容结构化，确保不添加、不篡改、不遗漏。\n"
            "第二层（知识提炼）：从结构化内容中提取框架、模型、洞察——\n"
            "这些是可以脱离具体案例、迁移到其他场景使用的知识。"
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

    def build_user_prompt(self, transcript: str, title: Optional[str] = None) -> str:
        """
        组装 user prompt

        Args:
            transcript: 转写文本
            title: 视频标题（可选）

        Returns:
            完整 user prompt
        """
        parts = []

        # 指令
        instruction = "请根据以下视频转写文本，生成结构化的学习笔记。"
        if title:
            instruction += f"\n\n视频标题：{title}"
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
        parts.append(
            "## 修正要求\n\n"
            "1. 针对上述每个问题，逐一修正\n"
            "2. 不要重写整篇笔记，只修改有问题的部分\n"
            "3. 修正后请确保其他部分没有被破坏\n"
            "4. 修正完成后，对照硬约束（R1-R9）再自查一遍"
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
                       'R7_框架完整性', 'R8_洞察可行动性', 'R9_分层准确性']

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
        """构建输出格式要求段落"""
        if self.format_example:
            # 从参考笔记中提取结构（取前 80 行作为模板骨架）
            lines = self.format_example.split('\n')
            skeleton_lines = []
            in_content = False
            for line in lines:
                # 只保留标题行和结构标记
                if line.startswith('#') or line.startswith('---') or line.startswith('> **'):
                    skeleton_lines.append(line)
                    in_content = True
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
                "- 底部标注: `*笔记整理时间：{YYYY-MM-DD}*` + `*学习来源：原视频音频转写*`"
            )
        else:
            return (
                "## 输出格式要求\n\n"
                "请按以下结构输出笔记：\n"
                "1. `# {标题}` — 课程标题\n"
                "2. `> **课程定位**：{一句话概括}`\n"
                "3. `## 一、课程核心观点` — 按主题分节，含要点列表\n"
                "4. `## N、{主题}` — 按内容展开（2-5 个主题节）\n"
                "5. `## 学习总结` — 核心收获 + 行动清单 + 金句摘录\n"
                "6. `## 知识框架提炼` — 从内容中提取的可迁移框架（见下方格式）\n"
                "7. `## 可迁移洞察` — 可直接行动的洞察表格\n"
                "8. `## 思维模型` — 可复用的思维模型\n"
                "9. 底部标注时间和来源\n\n"
                "### 知识框架提炼格式\n"
                "```\n"
                "## 知识框架提炼\n"
                "### 框架 1：{名称}\n"
                "- **核心定义**: {一句话}\n"
                "- **组成要素**: {1. 2. 3.}\n"
                "- **适用场景**: {什么时候用}\n"
                "- **原文依据**: {引用原文}\n"
                "```\n\n"
                "### 可迁移洞察格式\n"
                "```\n"
                "## 可迁移洞察\n"
                "| 洞察 | 做什么 | 何时用 | 预期效果 |\n"
                "|------|--------|--------|----------|\n"
                "| {洞察名} | {具体行动} | {触发条件} | {可衡量结果} |\n"
                "```\n\n"
                "### 思维模型格式\n"
                "```\n"
                "## 思维模型\n"
                "- **模型名**: {名称}\n"
                "- **输入→输出**: {什么输入会产生什么输出}\n"
                "- **反面案例**: {什么时候不适用}\n"
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
            "不得将个案经验包装为通用原则。"
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
