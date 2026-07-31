# -*- coding: utf-8 -*-
"""
NoteForge 知识合成质量验证

从 SynthesisEngine 提取的合成质量验证逻辑，独立为可测试的模块级函数。

职责：
  - 来源标注真实性检查（验证「第X集」引用是否对应真实存在的笔记）
  - 交叉引用集数一致性检查

注意：不检查 prompt 要求 LLM 生成的章节名/标题数/金句数等可自满足项
（那些是 prompt 遵从性检查，不是知识质量检查，LLM 可轻易满足）。
"""

import re
from pathlib import Path
from typing import List


def validate_synthesis(synthesis_text: str, note_paths: List[str]) -> List[str]:
    """验证合成文档的引用一致性（真实检查，非 prompt 遵从性）"""
    issues = []
    available_eps = set()

    # 1. 来源标注检查 — 提取所有「第X集」引用，验证是否存在对应笔记
    ep_refs = re.findall(r'第\s*(\d+)\s*集', synthesis_text)
    if not ep_refs:
        issues.append("未找到任何「第X集」来源标注")
    else:
        for p in note_paths:
            stem = Path(p).stem
            # 支持两种命名：文件名中"第X集" 或 "epXX" 格式
            m = re.search(r'第(\d+)集', stem)
            if m:
                available_eps.add(m.group(1))
            m = re.search(r'ep(\d+)', stem, re.IGNORECASE)
            if m:
                available_eps.add(m.group(1))
        missing_eps = set(ep_refs) - available_eps
        if missing_eps:
            issues.append(
                f"引用了不存在的集数: {', '.join(sorted(missing_eps))}"
                + (f"（实际集数: {sorted(available_eps)}）" if available_eps else "")
            )

    # 2. 交叉引用集数一致性 — 检查合成中跨集引用的集数是否都在源笔记中
    cross_ep_refs = re.findall(r'(?:前|后|上|下)\s*(\d+)\s*集', synthesis_text)
    if cross_ep_refs and available_eps:
        missing_cross = set(cross_ep_refs) - available_eps
        if missing_cross:
            issues.append(
                f"跨集引用了不存在的集数: {', '.join(sorted(missing_cross))}"
            )

    return issues
