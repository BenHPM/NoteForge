# -*- coding: utf-8 -*-
"""
NoteForge 启发式质量指标（零 API 成本）

从 quality_gate.py 提取的 QualityMetrics dataclass + 6 个启发式计算。
独立模块，方便后续扩展新的指标维度。
"""

import re
from dataclasses import dataclass


@dataclass
class QualityMetrics:
    """启发式质量指标（零 API 成本）"""
    compression_ratio: float    # 笔记字数/原文字数（理想 10-30%）
    structure_score: float      # 结构丰富度（0-1）
    info_density: float         # 信息密度（0-1）
    readability_score: float    # 可读性（0-1）
    quote_ratio: float          # 原话引用占比（0-1）
    action_specificity: float   # 行动清单具体性（0-1）
    overall_richness: float     # 综合丰富度（加权平均）

    def to_dict(self):
        return {
            "compression_ratio": round(self.compression_ratio, 3),
            "structure_score": round(self.structure_score, 2),
            "info_density": round(self.info_density, 2),
            "readability_score": round(self.readability_score, 2),
            "quote_ratio": round(self.quote_ratio, 3),
            "action_specificity": round(self.action_specificity, 2),
            "overall_richness": round(self.overall_richness, 2),
        }


def compute_metrics(note_text: str, source_text: str,
                    body_text: str) -> QualityMetrics:
    """计算启发式质量指标"""
    lines = note_text.split('\n')
    total_lines = len([l for l in lines if l.strip()])

    # 1. 压缩比：笔记字数/原文字数（理想 10-30%）
    note_chars = len(body_text.replace('\n', '').replace(' ', ''))
    source_chars = len(source_text.replace('\n', '').replace(' ', ''))
    compression = note_chars / max(source_chars, 1)

    # 2. 结构丰富度（标题数、列表数、表格数、引用数）
    headings = sum(1 for l in lines if re.match(r'^#{1,6}\s', l))
    lists = sum(1 for l in lines if re.match(r'^\s*[-*]\s', l))
    tables = sum(1 for l in lines if l.strip().startswith('|') and '|' in l[1:])
    quotes = sum(1 for l in lines if l.strip().startswith('>'))
    # 归一化：理想笔记应有 5-15 个标题、10-30 个列表项、0-5 个表格、2-8 个引用
    structure = min(1.0, (
        min(headings / 8, 1.0) * 0.3 +
        min(lists / 15, 1.0) * 0.3 +
        min(tables / 3, 1.0) * 0.2 +
        min(quotes / 4, 1.0) * 0.2
    ))

    # 3. 信息密度（不同概念/总句数）
    sentences = [l.strip() for l in lines if len(l.strip()) > 10]
    # 提取中文词组（2-4字）作为概念代理
    all_words = []
    for s in sentences:
        all_words.extend(re.findall(r'[一-鿿]{2,4}', s))
    unique_words = set(all_words)
    density = min(1.0, len(unique_words) / max(len(sentences) * 2, 1))

    # 4. 可读性（段落质量 + 结构多样性 + 信息密度）
    readability = 0.0  # 默认值，防止 paragraphs 为空时 UnboundLocalError
    # 智能段落检测：按内容逻辑分组（列表组、表格组、引用组各算一个段落）
    paragraphs = []
    current_para = []
    current_type = 'text'  # text / list / table / quote

    for l in lines:
        stripped = l.strip()
        if not stripped:
            if current_para:
                paragraphs.append((current_type, current_para))
                current_para = []
                current_type = 'text'
            continue

        # 判断行类型
        if stripped.startswith('- ') or stripped.startswith('* '):
            line_type = 'list'
        elif stripped.startswith('|'):
            line_type = 'table'
        elif stripped.startswith('>'):
            line_type = 'quote'
        elif stripped.startswith('#'):
            line_type = 'heading'
        else:
            line_type = 'text'

        # 类型切换时结束当前段落（但连续同类型列表/表格/引用合并）
        if current_type != line_type and not (
            current_type == 'list' and line_type == 'list'
        ):
            if current_para:
                paragraphs.append((current_type, current_para))
                current_para = []

        current_type = line_type
        current_para.append(stripped)

    if current_para:
        paragraphs.append((current_type, current_para))

    if paragraphs:
        # 只评估文本段落的长度（列表/表格/引用不参与段落长度评分）
        text_paras = [p for t, p in paragraphs if t == 'text' and len(p) >= 2]
        if text_paras:
            ideal_count = sum(1 for p in text_paras if 3 <= len(p) <= 8)
            long_count = sum(1 for p in text_paras if len(p) > 10)
            para_quality = (
                ideal_count / len(text_paras) * 0.6
                + max(0, 1 - long_count / len(text_paras)) * 0.4
            )
        else:
            para_quality = 0.8  # 纯列表/表格笔记（如实操步骤）

        # 结构多样性：标题、列表、表格、引用混合使用
        type_set = set(t for t, _ in paragraphs)
        structure_variety = min(1.0, len(type_set) / 3.0)

        # 内容密度：非空段落中有实质内容的比例
        substantial = sum(1 for t, p in paragraphs if len(p) >= 2 or t in ('list', 'table'))
        density_per_para = substantial / max(len(paragraphs), 1)

        readability = (
            para_quality * 0.45 +
            structure_variety * 0.30 +
            density_per_para * 0.25
        )

    # 5. 原话引用占比（引号/引用块行数/总行数）
    quote_lines = sum(
        1 for l in lines
        if l.strip().startswith('>')
        or '"' in l or '"' in l or '「' in l
    )
    quote_ratio = quote_lines / max(total_lines, 1)

    # 6. 行动清单具体性（支持复选框、数字编号、列表三种格式）
    action_patterns = [
        r'^\s*-\s*\[[ x]\]\s*',          # - [ ] 格式
        r'^\s*\d+[.、]\s*(?=[一-鿿])',  # 1. 格式（后跟中文）
        r'^\s*[-*]\s+(?:练习|建立|制作|阅读|学习|尝试|使用|分析|拆解|记录|观察|关注)',
    ]
    # 先找到行动清单段落
    in_action_section = False
    action_lines = []
    for l in lines:
        if '行动清单' in l or '行动项' in l or '行动建议' in l:
            in_action_section = True
            continue
        if in_action_section:
            if l.startswith('##') or l.startswith('---'):
                in_action_section = False
                continue
            if l.strip() and any(re.match(p, l) for p in action_patterns):
                action_lines.append(l)
            elif l.strip() and not l.startswith('#') and not l.startswith('>'):
                # 段落内的行动描述也收集
                if any(v in l for v in ['练习', '建立', '制作', '阅读', '尝试', '每周', '每天', '每月']):
                    action_lines.append(l)
    if action_lines:
        # 具体行动：包含数字、时间、频率、工具名
        concrete_patterns = [
            r'\d+\s*(?:小时|分钟|天|周|月|次|篇|条)',
            r'(?:每天|每周|每月|每次|定期)',
            r'(?:建立|制作|创建|使用|打开|检查)',
        ]
        concrete_count = sum(
            1 for a in action_lines
            if any(re.search(p, a) for p in concrete_patterns)
        )
        action_specificity = concrete_count / len(action_lines)
    else:
        action_specificity = 0.0

    # 综合丰富度（加权平均）
    overall = (
        min(compression / 0.2, 1.0) * 0.15 +  # 压缩比（达到20%即满分）
        structure * 0.25 +
        density * 0.20 +
        readability * 0.20 +
        min(quote_ratio / 0.1, 1.0) * 0.10 +  # 引用占比（达到10%即满分）
        action_specificity * 0.10
    )

    return QualityMetrics(
        compression_ratio=compression,
        structure_score=structure,
        info_density=density,
        readability_score=readability,
        quote_ratio=quote_ratio,
        action_specificity=action_specificity,
        overall_richness=overall,
    )
