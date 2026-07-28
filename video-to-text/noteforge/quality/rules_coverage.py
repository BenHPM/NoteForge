# -*- coding: utf-8 -*-
"""
NoteForge 覆盖度与结构质量规则（R4, R5, R6, R7）

与内容完整性相关的规则检查函数：
- R4: 禁止关键概念简化失真
- R5: 覆盖度底线
- R6: 术语一致性
- R7: 框架完整性

辅助函数：
- extract_framework_section: R7 的辅助函数
"""

import re
import logging
from noteforge.quality.models import Issue, RuleResult

logger = logging.getLogger('noteforge.quality')


# ----------------------------------------------------------
# 辅助函数
# ----------------------------------------------------------

def extract_framework_section(text: str, pattern: str) -> str:
    """提取包含框架的段落（从第一个匹配到最后一个匹配之间的文本）"""
    matches = list(re.finditer(pattern, text))
    if len(matches) < 3:
        return ""
    start = max(0, matches[0].start() - 100)
    end = min(len(text), matches[-1].end() + 100)
    return text[start:end]


# ----------------------------------------------------------
# R4: 禁止关键概念简化失真
# ----------------------------------------------------------
def check_concept_distortion(key_concepts: dict, note_text: str) -> RuleResult:
    """检查笔记中的专业概念是否保留了关键限定词"""
    issues = []

    for concept, required_keywords in key_concepts.items():
        if concept in note_text:
            # 找到概念出现的上下文（前后各200字符）
            for match in re.finditer(re.escape(concept), note_text):
                start = max(0, match.start() - 200)
                end = min(len(note_text), match.end() + 200)
                context = note_text[start:end]

                # 计算上下文丰富度：如果说明文字足够多（>100字），说明概念在被深度讨论
                context_length = len(context.replace(concept, '').strip())

                # 检查必有关键词
                missing = [
                    kw for kw in required_keywords
                    if kw not in context
                ]

                if missing:
                    # 上下文丰富时（概念正在被深度讨论），降低严重度
                    # 上下文 <200 字时概念可能只是简单提及，容易失真
                    if context_length < 200:
                        line_num = note_text[:match.start()].count('\n') + 1
                        severity = "major" if context_length < 80 else "medium"
                        issues.append(Issue(
                            rule_id="R4",
                            rule_name="禁止关键概念简化失真",
                            severity=severity,
                            line_range=f"L{line_num}",
                            description=f"概念'{concept}'丢失关键限定词: {', '.join(missing)}",
                            suggestion=f"请在'{concept}'的描述中补充: {', '.join(missing)}"
                        ))

    score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.15)
    return RuleResult("R4", "禁止关键概念简化失真", score, len(issues) == 0, issues)


# ----------------------------------------------------------
# R5: 覆盖度底线
# ----------------------------------------------------------
def check_coverage(note_text: str, source_text: str,
                   fatal_threshold: float = 0.30,
                   major_threshold: float = 0.80) -> RuleResult:
    """检查笔记覆盖了原文多少议题"""
    issues = []

    # 从原文中提取关键议题（章节标题、关键段落标记）
    chapter_patterns = [
        # 听悟格式: **HH:MM 标题**
        r'\*\*(\d{2}:\d{2}\s+.+?)\*\*',
        # 通用标题格式（排除元数据行）
        r'^#{1,3}\s+(?!(?:转写|笔记|学习|课程|第\d+集|视频|来源|时间|格式))(.+)$',
    ]

    source_chapters = []
    for pattern in chapter_patterns:
        source_chapters.extend(re.findall(pattern, source_text, re.MULTILINE))

    # 去重
    source_chapters = list(dict.fromkeys(source_chapters))

    if not source_chapters:
        # 无章节标记时，改用段落级关键词覆盖度
        # 将源文本按空行分段，提取每段的关键词，检查笔记是否覆盖了主要段落
        source_paragraphs = [p.strip() for p in re.split(r'\n\s*\n', source_text)
                             if len(p.strip()) > 50]  # 忽略过短的段落
        if not source_paragraphs:
            # 源文本几乎没有有效内容，无法评估覆盖度
            ratio = 1.0
        else:
            covered_paragraphs = 0
            for para in source_paragraphs:
                # 提取段落中的关键词（2字以上的中文词组）
                try:
                    import jieba
                    para_tokens = [t for t in jieba.lcut(para) if len(t) >= 2]
                except ImportError:
                    para_tokens = []
                if not para_tokens:
                    continue
                # 检查段落关键词在笔记中的覆盖率
                matched = sum(1 for t in para_tokens if t in note_text)
                # 超过 30% 的关键词被覆盖则认为该段落已覆盖
                if matched / len(para_tokens) >= 0.30:
                    covered_paragraphs += 1
            ratio = covered_paragraphs / len(source_paragraphs)
    else:
        # 检查每个章节标题是否在笔记中被覆盖
        covered = 0
        for chapter in source_chapters:
            chapter_key = chapter[:15].strip()
            if not chapter_key:
                continue
            # 精确匹配：整个章节标题在笔记中出现
            if chapter_key in note_text:
                covered += 1
            else:
                # 模糊匹配：使用 jieba 分词取 2 字以上词，比滑动窗口更准确
                try:
                    import jieba
                    jieba_tokens = [t for t in jieba.lcut(chapter_key) if len(t) >= 2]
                except ImportError:
                    jieba_tokens = []

                def _try_match(tokens, text):
                    # 策略 1: 单个词匹配
                    for t in tokens:
                        if t in text:
                            return True
                    # 策略 2: 连续两个词拼接匹配（jieba 常将复合词拆开）
                    for i in range(len(tokens) - 1):
                        combined = tokens[i] + tokens[i + 1]
                        if combined in text:
                            return True
                    return False

                matched = _try_match(jieba_tokens, note_text)

                if not matched:
                    # jieba 方案未命中，回退到滑动窗口
                    sub_keys = []
                    for n in range(2, min(5, len(chapter_key) + 1)):
                        for i in range(len(chapter_key) - n + 1):
                            sub_keys.append(chapter_key[i:i + n])
                    sub_keys = sorted(set(sub_keys), key=len, reverse=True)[:4]
                    matched = any(kw in note_text for kw in sub_keys)

                if matched:
                    covered += 1
        ratio = covered / len(source_chapters)

    # 双阈值: < 30% fatal（严重缺失），< 80% major（一般缺失）
    if ratio < fatal_threshold:
        issues.append(Issue(
            rule_id="R5",
            rule_name="覆盖度底线",
            severity="fatal",
            line_range="全文",
            description=f"笔记覆盖率为 {ratio:.1%}，严重低于{fatal_threshold:.0%}下限。原文约{len(source_chapters) if source_chapters else 'N/A'}个议题，笔记仅覆盖约{int(ratio * len(source_chapters))}个",
            suggestion="笔记可能为空或严重不完整，请检查 LLM 生成是否成功"
        ))
    elif ratio < major_threshold:
        issues.append(Issue(
            rule_id="R5",
            rule_name="覆盖度底线",
            severity="major",
            line_range="全文",
            description=f"笔记覆盖率为 {ratio:.1%}，低于{major_threshold:.0%}底线。原文约{len(source_chapters) if source_chapters else 'N/A'}个议题，笔记仅覆盖约{int(ratio * len(source_chapters))}个",
            suggestion="请对照原文章节列表检查遗漏的议题并补充"
        ))

    return RuleResult(
        "R5", "覆盖度底线",
        min(1.0, ratio / major_threshold),
        ratio >= fatal_threshold,  # fatal 阈值
        issues
    )


# ----------------------------------------------------------
# R6: 术语一致性
# ----------------------------------------------------------
def check_consistency(note_text: str) -> RuleResult:
    """检查笔记中术语表的定义是否与正文使用一致"""
    issues = []

    # 查找术语表区域
    term_table_pattern = r'\|.*?\|.*?\|'  # Markdown表格行
    term_tables = list(re.finditer(term_table_pattern, note_text, re.MULTILINE))

    if term_tables:
        # 提取术语表中的术语名和定义
        table_terms = {}
        for match in term_tables:
            row = match.group()
            parts = [p.strip() for p in row.split('|') if p.strip()]
            if len(parts) >= 2:
                term = parts[0].replace('**', '').strip()
                definition = parts[1].replace('**', '').strip()
                if term and definition and term not in ('术语', '解释', '---', '术语表'):
                    table_terms[term] = definition

        # 检查正文中对这些术语的使用
        body_text = note_text
        for table_match in term_tables:
            body_text = body_text.replace(table_match.group(), '')

        for term, definition in table_terms.items():
            if term in body_text:
                # 检查定义中的核心词是否与正文语境一致
                def_keywords = set(
                    w for w in definition.replace('(', ' ').replace(')', ' ').split()
                    if len(w) >= 2
                )
                # 对于"因子=不可解释为主"这类，检查正文是否出现矛盾的"可解释性[强高]"
                if "不可解释" in definition and re.search(r'可解释性\s*[强高]', body_text):
                    issues.append(Issue(
                        rule_id="R6",
                        rule_name="术语一致性",
                        severity="medium",
                        line_range="术语表/正文",
                        description=f"术语'{term}'定义为'{definition}'，但正文中出现了矛盾表述",
                        suggestion="请检查术语定义与正文描述是否一致"
                    ))

    score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.2)
    return RuleResult("R6", "术语一致性", score, len(issues) == 0, issues)


# ----------------------------------------------------------
# R7: 框架完整性
# ----------------------------------------------------------
def check_framework_completeness(note_text: str) -> RuleResult:
    """检查笔记中提取的框架是否保留了全部组成要素"""
    issues = []

    # 检测框架类段落（包含"步骤"、"要素"、"阶段"、"法"等关键词的列表）
    framework_markers = [
        (r'(?:第[一二三四五六七八九十\d]+步)', '步骤'),
        (r'(?:第[一二三四五六七八九十\d]+[点个阶段])', '阶段'),
        (r'(?:\d+[.、])\s*\S+', '编号列表'),
    ]

    # 检查是否有框架段落但要素过少
    for pattern, label in framework_markers:
        matches = re.findall(pattern, note_text)
        if len(matches) >= 5:
            # 找到一个有 5+ 要素的框架，检查是否有对应的详细说明
            # 如果框架步骤很多但每步描述很短（<20字），可能丢失了细节
            framework_section = extract_framework_section(note_text, pattern)
            if framework_section:
                short_steps = sum(
                    1 for line in framework_section.split('\n')
                    if re.match(r'\s*(?:\d+[.、]|第)', line.strip())
                    and len(line.strip()) < 20
                )
                total_steps = sum(
                    1 for line in framework_section.split('\n')
                    if re.match(r'\s*(?:\d+[.、]|第)', line.strip())
                )
                if total_steps > 0 and short_steps / total_steps > 0.5:
                    issues.append(Issue(
                        rule_id="R7",
                        rule_name="框架完整性",
                        severity="major",
                        line_range="框架段落",
                        description=f"框架包含 {total_steps} 个要素，但 {short_steps} 个描述过于简短（<20字），可能丢失关键细节",
                        suggestion="请为每个框架要素补充足够的描述，保留原文的关键限定词和条件"
                    ))

    score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.25)
    return RuleResult("R7", "框架完整性", score, len(issues) == 0, issues)
