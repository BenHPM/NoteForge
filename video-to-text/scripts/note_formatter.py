"""
NoteForge 笔记格式化模块 v2.0
功能:
- 后处理 LLM 输出，确保符合标准格式
- 根据内容类型自适应校验规则
- 校验笔记结构完整性
- 补全缺失的元数据页脚
"""

import re
from datetime import datetime
from typing import List, Optional
from pathlib import Path


# 内容类型的结构要求配置
CONTENT_TYPE_MARKERS = {
    'lecture': {
        'required': ['# ', '**课程定位**', '## ', '学习总结'],
        'expected': ['核心观点', '学习总结'],
    },
    'tutorial': {
        'required': ['# ', '## ', '学习总结'],
        'expected': ['课程核心观点', '学习总结'],
    },
    'interview': {
        'required': ['# ', '## ', '学习总结'],
        'expected': ['核心观点', '学习总结'],
    },
    'podcast': {
        'required': ['# ', '## '],
        'expected': ['核心话题', '学习总结'],
    },
    'meeting': {
        'required': ['# '],
        'expected': ['决策', '待办'],
    },
}


class NoteFormatter:
    """笔记格式化器（v2.0 content_type 感知）"""

    # 默认必须包含的结构标记（向后兼容）
    REQUIRED_MARKERS = [
        '# ',            # 标题
        '**课程定位**',   # 或 **课程定位
        '## ',           # 至少一个二级标题
        '学习总结',      # 学习总结段
    ]

    # 期望的结构节（按顺序）
    EXPECTED_SECTIONS = [
        '核心观点',
        '学习总结',
    ]

    def _get_markers(self, content_type: str) -> dict:
        """根据内容类型获取结构标记要求"""
        return CONTENT_TYPE_MARKERS.get(content_type, CONTENT_TYPE_MARKERS['lecture'])

    def format(self, raw_output: str, title: Optional[str] = None,
               transcript_path: Optional[str] = None,
               mode: str = 'notes',
               content_type: Optional[str] = None,
               transcript_text: Optional[str] = None) -> str:
        """
        后处理 LLM 输出

        Args:
            raw_output: LLM 原始输出
            title: 笔记标题（如果 LLM 未生成则补全）
            transcript_path: 转写文件路径（用于元数据）
            mode: 生成模式 ('notes' | 'meeting')
            content_type: 内容类型 (lecture/tutorial/interview/podcast)
            transcript_text: 转写原文（用于质量声明自动生成）

        Returns:
            格式化后的笔记
        """
        note = raw_output.strip()

        # 确定实际内容类型
        ct = content_type or ('meeting' if mode == 'meeting' else 'lecture')

        # 1. 确保以标题开头
        note = self._ensure_title(note, title)

        # 2. 确保有课程定位行（仅 lecture 模式）
        if ct == 'lecture' and mode != 'meeting':
            note = self._ensure_course_position(note)

        # 3. 确保有分隔线
        note = self._ensure_dividers(note)

        # 4. 确保有元数据页脚
        note = self._ensure_footer(note, transcript_path)

        # 5. 确保有转写质量声明
        if mode != 'meeting':
            note = self._ensure_quality_statement(note, transcript_text)

        # 6. 清理多余的空行
        note = re.sub(r'\n{4,}', '\n\n\n', note)

        return note

    def validate_structure(self, note: str, mode: str = 'notes',
                           content_type: Optional[str] = None) -> List[str]:
        """
        校验笔记结构完整性（根据内容类型自适应）

        Args:
            note: 笔记文本
            mode: 生成模式 ('notes' | 'meeting')
            content_type: 内容类型

        Returns:
            问题列表（空列表表示无问题）
        """
        issues: List[str] = []

        # 检查标题
        if not note.startswith('# '):
            issues.append("笔记应以一级标题(#)开头")

        # meeting 模式使用不同的结构检查
        if mode == 'meeting':
            if '决策' not in note and '待办' not in note:
                issues.append("会议纪要缺少决策或待办事项段落")
            return issues

        # 根据内容类型获取结构要求
        ct = content_type or 'lecture'
        markers = self._get_markers(ct)

        # 检查必须标记
        for marker in markers.get('required', self.REQUIRED_MARKERS):
            if marker not in note:
                issues.append(f"缺少必要标记: '{marker}'")

        # 检查是否有学习总结
        if '核心收获' not in note and '学习总结' not in note:
            issues.append("缺少学习总结/核心收获段落")

        # 检查是否有行动清单
        if '- [ ]' not in note and '- []' not in note:
            issues.append("缺少行动清单（- [ ] 格式）")

        # 检查是否有金句摘录（lecture/tutorial 强制，其他类型可选）
        if ct in ('lecture', 'tutorial'):
            if '金句' not in note and '> "' not in note and "> '" not in note:
                issues.append("缺少金句摘录段落")

        # 检查元数据页脚
        if '笔记整理时间' not in note:
            issues.append("缺少笔记整理时间标注")

        if '学习来源' not in note:
            issues.append("缺少学习来源标注")

        # 检查标题层级（不能跳级）
        headings = re.findall(r'^(#{1,6})\s', note, re.MULTILINE)
        if headings:
            levels = [len(h) for h in headings]
            if levels[0] != 1:
                issues.append("笔记应以一级标题(#)开头")
            for i in range(1, len(levels)):
                if levels[i] > levels[i-1] + 1:
                    issues.append(f"标题层级跳跃: 从 H{levels[i-1]} 直接到 H{levels[i]}")

        return issues

    def _ensure_title(self, note: str, title: Optional[str] = None) -> str:
        """确保笔记以 # 标题开头"""
        if note.startswith('# '):
            return note

        if title:
            return f"# {title}\n\n{note}"

        # 尝试从内容中提取标题
        first_line = note.split('\n')[0].strip()
        if first_line and not first_line.startswith('#'):
            return f"# {first_line}\n\n" + '\n'.join(note.split('\n')[1:])

        return note

    def _ensure_course_position(self, note: str) -> str:
        """确保有课程定位行（lecture 模式）"""
        if '课程定位' in note:
            return note

        # 不再插入"（待补充）"占位符——由 LLM 在生成时完成
        # 如果 LLM 未生成课程定位，格式化器不做补充
        return note

    def _ensure_dividers(self, note: str) -> str:
        """确保课程定位后有分隔线"""
        lines = note.split('\n')
        result: List[str] = []
        for i, line in enumerate(lines):
            result.append(line)
            # 在课程定位行后添加分隔线
            if '课程定位' in line and line.startswith('>'):
                # 检查下一行是否已是分隔线
                next_line = lines[i + 1].strip() if i + 1 < len(lines) else ''
                if next_line != '---':
                    result.append('')
                    result.append('---')
        return '\n'.join(result)

    def _ensure_footer(self, note: str, transcript_path: Optional[str] = None) -> str:
        """确保有元数据页脚（日期始终使用今天）"""
        today = datetime.now().strftime('%Y-%m-%d')
        source = '原视频音频转写'
        if transcript_path:
            source = f'原视频音频转写 ({Path(transcript_path).name})'

        footer = (
            f"\n---\n\n"
            f"*笔记整理时间：{today}*\n"
            f"*学习来源：{source}*"
        )

        # 如果已有页脚，替换日期（LLM 可能填入了转写中的旧日期）
        if '笔记整理时间' in note:
            note = re.sub(
                r'\*笔记整理时间：\d{4}-\d{2}-\d{2}\*',
                f'*笔记整理时间：{today}*',
                note,
            )
            return note

        # 移除末尾空白后追加
        note = note.rstrip() + footer
        return note

    def _ensure_quality_statement(self, note: str,
                                   transcript_text: Optional[str] = None) -> str:
        """确保有转写质量声明（自动分析转写文本）"""
        if '转写质量' in note:
            return note

        if transcript_text:
            # 自动分析转写质量
            noise_count = len(re.findall(r'\[无法识别片段\]', transcript_text))
            has_timestamps = bool(re.search(r'\[\d{2}:\d{2}\]', transcript_text))

            if noise_count == 0:
                quality = '良好'
            elif noise_count <= 5:
                quality = '一般'
            else:
                quality = '较差'

            issue_desc = f'{noise_count}处无法识别' if noise_count > 0 else '无明显噪声'
            if has_timestamps:
                issue_desc += '，含时间戳'

            # 检查笔记中人名是否在转写中出现（简单校验）
            person_pattern = re.compile(r'[「"]?\s*([^\s「」""，。]{2,4})\s*[」"]?\s*(?:说|认为|指出|表示)')
            note_names = set(m.group(1) for m in person_pattern.finditer(note))
            checked_names = []
            unchecked_names = []
            for name in note_names:
                if len(name) < 2:
                    continue
                if name in transcript_text:
                    checked_names.append(name)
                else:
                    # 尝试模糊匹配
                    fuzzy_pairs = {'翟': '狄', '狄': '翟'}
                    found = False
                    for src, tgt in fuzzy_pairs.items():
                        if src in name and name.replace(src, tgt) in transcript_text:
                            found = True
                            break
                    if found:
                        checked_names.append(name)
                    else:
                        unchecked_names.append(name)

            name_status = '已校对' if not unchecked_names else f'部分校对（未确认：{"、".join(unchecked_names)}）'

            statement = (
                f"\n\n*转写质量：{quality} | "
                f"已知问题：{issue_desc} | "
                f"人名校对：{name_status}*"
            )
        else:
            statement = "\n\n*转写质量：未检测 | 人名校对：未执行*"

        return note.rstrip() + statement
