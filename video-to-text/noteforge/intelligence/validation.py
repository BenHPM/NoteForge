# -*- coding: utf-8 -*-
"""
NoteForge 知识合成质量验证

从 SynthesisEngine 提取的合成质量验证逻辑，独立为可测试的模块级函数。

职责：
  - 合成文档结构检查
  - 来源标注检查
  - 交叉关联检查
  - 信息密度检查
  - 金句检查
"""

import re
from pathlib import Path
from typing import List


def validate_synthesis(synthesis_text: str, note_paths: List[str]) -> List[str]:
    """验证合成文档的质量"""
    issues = []

    # 1. 基本结构检查
    required_sections = ['思维模型', '方法论', '行动', '学习路径', '金句']
    for section in required_sections:
        if section not in synthesis_text:
            issues.append(f"缺少必要节: {section}")

    # 2. 来源标注检查 — 提取所有「第X集」引用，验证是否存在对应笔记
    ep_refs = re.findall(r'第\s*(\d+)\s*集', synthesis_text)
    if not ep_refs:
        issues.append("未找到任何「第X集」来源标注")
    else:
        # 检查引用的集数是否在笔记文件中存在
        available_eps = set()
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
