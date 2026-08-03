# -*- coding: utf-8 -*-
"""
NoteForge 低价值笔记检测（招生简章/上线通知/无内容宣传等）

用于两条路径：
  - 飞书同步（feishu_sync）：低价值笔记不同步到飞书知识库，本地保留
  - 知识合成（synthesis）：低价值笔记不进入跨集知识体系的源笔记

分层检测：
  1. 文件名标记（保守，只含明确非知识内容词：简章/招生/公告/预告/报名/宣传片）
  2. 内容标记（LLM 自述"无知识可提炼"的信号：无法生成结构化学习笔记/招生宣传文案/上线通知等）

边界处理：
  - "上架啦"类（如新书发售讲座）虽有宣传标题但内容常有干货，不做文件名过滤，
    由内容标记决定：内容自述为纯宣传/通知才拦截。
"""

import fnmatch
from typing import List, Optional

# 内容级低价值标记：LLM 在笔记中自述"无知识可提炼/纯宣传/上线通知"的信号。
# 这些是明确的自述式拒绝/声明，误报风险低。
LOW_VALUE_CONTENT_MARKERS: List[str] = [
    "无法生成符合用户要求",   # LLM 拒绝生成结构化学习笔记
    "无法执行知识提炼",        # LLM 拒绝知识提炼
    "招生宣传文案",            # 招生广告
    "招生简章",
    "课程上线通知",            # 课程上架通知
    "上线通知",
    "报名简章",
]

# 文件名低价值标记（保守，与 config feishu.junk_patterns 互补；此列表为代码内置兜底）
LOW_VALUE_FILENAME_MARKERS: List[str] = [
    "简章", "招生", "公告", "预告", "报名", "宣传片", "上线通知",
]


def is_low_value_note(
    filename: str,
    content: str = "",
    extra_filename_patterns: Optional[List[str]] = None,
) -> bool:
    """判断一篇笔记是否为低价值内容（无知识含量，应从同步/合成中排除）。

    Args:
        filename: 笔记文件名
        content: 笔记内容（可为空串，跳过内容检测）
        extra_filename_patterns: 额外文件名 fnmatch 模式（如 config 的 junk_patterns）

    Returns:
        True 表示低价值笔记，应从飞书同步 / 跨集合成源笔记中排除。
    """
    # 1. 内置文件名标记（保守）
    if any(m in filename for m in LOW_VALUE_FILENAME_MARKERS):
        return True
    # 2. 配置化文件名模式（用户可调）
    if extra_filename_patterns and any(
        fnmatch.fnmatch(filename, p) for p in extra_filename_patterns
    ):
        return True
    # 3. 内容标记（LLM 自述无知识可提炼）
    if content and any(m in content for m in LOW_VALUE_CONTENT_MARKERS):
        return True
    return False
