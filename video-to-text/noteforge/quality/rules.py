# -*- coding: utf-8 -*-
"""
NoteForge 质量规则检查函数（R1-R12）

提取自 QualityGate 类的 _check_xxx 方法，转为独立模块级函数。
每个函数接收必要的参数（key_concepts / fabricated_patterns），
不再依赖 self。

规则按语义域拆分为三个模块：
- rules_factual: 事实性规则（R1, R2, R3, R12）+ 数字辅助函数
- rules_coverage: 覆盖度与结构规则（R4, R5, R6, R7）+ 框架辅助函数
- rules (本文件): 洞察与引用规则（R8, R9, R10, R11）

本文件 re-export 所有函数，保持向后兼容：
  from noteforge.quality.rules import check_fabricated_data  # 仍然可用
"""

import re
import logging
from noteforge.quality.models import Issue, RuleResult

# Re-export 事实性规则（R1, R2, R3, R12）+ 辅助函数
from noteforge.quality.rules_factual import (  # noqa: F401
    check_fabricated_data,
    check_unmarked_additions,
    check_semantic_reversal,
    check_name_number_consistency,
    num_to_chinese,
    number_in_source,
)

# Re-export 覆盖度与结构规则（R4, R5, R6, R7）+ 辅助函数
from noteforge.quality.rules_coverage import (  # noqa: F401
    check_concept_distortion,
    check_coverage,
    check_consistency,
    check_framework_completeness,
    extract_framework_section,
)

logger = logging.getLogger('noteforge.quality')


# ----------------------------------------------------------
# R8: 洞察可行动性
# ----------------------------------------------------------
def check_insight_actionability(note_text: str) -> RuleResult:
    """检查洞察是否包含具体行动指引"""
    issues = []

    # 检测空洞总结模式（只有方向性描述，没有具体行动）
    vague_patterns = [
        (r'(?:要|应该|需要)\s*(?:重视|关注|注意|加强|提升|提高)\s*\S{2,6}',
         '空洞的方向性总结'),
        (r'(?:关键是|重要的是|核心是)\s*(?:要|应该)\s*\S{2,6}',
         '缺乏具体步骤的断言'),
    ]

    # 只检查"洞察"相关段落
    insight_sections = re.findall(
        r'(?:洞察|行动|建议|总结).*?(?=\n##|\n---|\Z)',
        note_text, re.DOTALL
    )

    for section in insight_sections:
        for pattern, desc in vague_patterns:
            for match in re.finditer(pattern, section):
                # 检查该行后面是否有具体行动（如"怎么做"）
                line_start = section.rfind('\n', 0, match.start()) + 1
                line_end = section.find('\n', match.end())
                if line_end == -1:
                    line_end = len(section)
                full_line = section[line_start:line_end]

                # 如果行长度很短且没有具体动词，标记为问题
                action_verbs = ['执行', '完成', '检查', '记录', '列出',
                                '练习', '使用', '按照', '通过']
                has_action = any(v in full_line for v in action_verbs)
                if len(full_line) < 50 and not has_action:
                    line_num = note_text[:note_text.find(section) + match.start()].count('\n') + 1
                    issues.append(Issue(
                        rule_id="R8",
                        rule_name="洞察可行动性",
                        severity="major",
                        line_range=f"L{line_num}",
                        description=f"疑似空洞总结: '{full_line[:60]}'",
                        suggestion="请补充具体行动步骤：做什么、何时做、预期效果"
                    ))

    score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.2)
    return RuleResult("R8", "洞察可行动性", score, len(issues) == 0, issues)


# ----------------------------------------------------------
# R9: 分层准确性
# ----------------------------------------------------------
def check_layering_accuracy(note_text: str) -> RuleResult:
    """检查是否正确区分表面内容和可迁移知识"""
    issues = []

    # 检测将个案包装为通用原则的模式
    generalization_patterns = [
        (r'(?:所有|任何|每个|凡是)\s*(?:人|创作者|导演|学习者)\s*(?:都|应该|必须)',
         '过度泛化'),
        (r'(?:永远|绝对|一定)\s*(?:要|不能|不要)',
         '绝对化表述'),
    ]

    # 引用上下文模式（排除引用原话的场景）
    quote_patterns = [
        r'[「」]',                  # 日文引号
        r'"[^"]*"',                # 英文双引号
        r"'[^']*'",               # 英文单引号
        r'>\s*["「\']',            # Markdown 引用块
        r'(?:讲师|老师|嘉宾|主持人|他说|她说|原文)[说道讲认为]:?\s*',
    ]
    quote_re = re.compile('|'.join(quote_patterns))

    for pattern, desc in generalization_patterns:
        for match in re.finditer(pattern, note_text):
            line_num = note_text[:match.start()].count('\n') + 1

            # 检查是否在引用上下文中（更大窗口 400 字符）
            broader_start = max(0, match.start() - 400)
            broader_context = note_text[broader_start:match.end() + 100]
            # 如果附近有引号标记，可能是讲师原话，降低严重度
            is_in_quote = bool(quote_re.search(broader_context))

            # 检查上下文是否标注了适用范围
            context_start = max(0, match.start() - 200)
            context = note_text[context_start:match.end() + 100]
            scope_markers = ['在.*场景', '对于.*来说', '在.*情况下',
                             '适用于', '限于', '主要']
            has_scope = any(re.search(m, context) for m in scope_markers)

            if not has_scope:
                if is_in_quote:
                    # 引用原话中的泛化表述，降级为 medium，加提示
                    issues.append(Issue(
                        rule_id="R9",
                        rule_name="分层准确性",
                        severity="medium",
                        line_range=f"L{line_num}",
                        description=f"引用中的泛化表述({desc}): '{match.group()}'（可能是讲师原话，请确认是否需要添加适用范围说明）",
                        suggestion="如为讲师原话可保留，但建议在后续段落标注适用范围"
                    ))
                else:
                    issues.append(Issue(
                        rule_id="R9",
                        rule_name="分层准确性",
                        severity="medium",
                        line_range=f"L{line_num}",
                        description=f"疑似过度泛化({desc}): '{match.group()}'",
                        suggestion="请标注适用范围和限制条件，区分个案经验和通用原则"
                    ))

    score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.15)
    return RuleResult("R9", "分层准确性", score, len(issues) == 0, issues)


# ----------------------------------------------------------
# R10: 时间线准确性
# ----------------------------------------------------------
def check_timeline_accuracy(note_text: str, source_text: str) -> RuleResult:
    """检测笔记中的时序表述是否与原文一致

    注意：笔记整理时对内容进行结构化重组是正常的（如"首先…然后…"），
    只有在笔记中声称了具体事件先后关系但原文无此时序时才标记。
    """
    issues = []

    # 检测时间顺序性表述
    timeline_patterns = [
        (r'(?:首先|第一步|第一阶段).*?(?:然后|接着|第二步)',
         '顺序性描述', 'first_then'),
        (r'(?:在.*之前|先.*后|前期.*后期)',
         '先后关系', 'before_after'),
        (r'(?:最初|一开始|开始时).*?(?:后来|最终|最后|结果)',
         '始末关系', 'initial_then'),
    ]

    for pattern, desc, tag in timeline_patterns:
        for match in re.finditer(pattern, note_text, re.DOTALL):
            matched_text = match.group()
            # 跳过标题行中的结构化时序词（如 "## 首先…然后…"）
            # 标题行以 # 开头或很短（<60字，是概述而非事实声明）
            line_start = note_text.rfind('\n', 0, match.start()) + 1
            line_end = note_text.find('\n', match.end())
            if line_end == -1:
                line_end = len(note_text)
            full_line = note_text[line_start:line_end].strip()
            if full_line.startswith('#') or len(full_line) < 60:
                continue

            # 检查时序关键词是否在原文中也有对应
            key_parts = re.findall(
                r'(首先|然后|接着|最后|最终|之前|之后|开始|后来|前期|后期)',
                matched_text
            )
            source_timeline = re.findall(
                r'(首先|然后|接着|最后|最终|之前|之后|开始|后来|前期|后期)',
                source_text
            )
            # 笔记有时序词但原文完全没有：可能虚构了时序关系
            if key_parts and not source_timeline:
                line_num = note_text[:match.start()].count('\n') + 1
                issues.append(Issue(
                    rule_id="R10",
                    rule_name="时间线准确性",
                    severity="medium",
                    line_range=f"L{line_num}",
                    description=f"疑似重组时序({desc}): '{matched_text[:80]}'",
                    suggestion="原文无明确时间标记，如为整理归纳可保留，但请核实事实先后是否正确"
                ))

    score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.2)
    return RuleResult("R10", "时间线准确性", score, len(issues) == 0, issues)


# ----------------------------------------------------------
# R11: 引用归属
# ----------------------------------------------------------
def check_quote_attribution(note_text: str, source_text: str) -> RuleResult:
    """检测引用/观点归属是否正确（是否张冠李戴）"""
    issues = []

    # 提取笔记中的引用模式：人名 + 说/认为/指出/表示...
    # 人名限制：2-4 个中文字符，必须在句首/标点后/空格后（避免误匹配词组中间）
    # 排除前导连词（但/而/且/又/就/也/还/都/却）
    quote_attribution_pattern = re.compile(
        r'(?:^|[\s，。、；：！？\n>|*「」""\-\[])(?:[但而且就也还都却])?'
        r'([一-鿿]{2,4})\s*'
        r'(?:说|认为|指出|表示|提到|强调|主张|分析|解释|总结道)[：:]?\s*',
        re.MULTILINE,
    )

    # 常见非人名词（排除匹配）
    non_person_words = {
        '原文', '笔记', '总结', '分析', '框架', '方法', '观点', '理论',
        '模型', '策略', '讲师', '老师', '嘉宾', '核心', '关键', '重要',
        '第一', '第二', '第三', '第四', '第五', '第六', '第七', '第八',
        '因此', '所以', '但是', '然而', '虽然', '如果', '因为', '通过',
        '大家', '我们', '他们', '你们', '自己', '什么', '这个', '那个',
        '一步', '句话', '方面', '层面', '角度', '维度', '阶段', '环节',
        '片子', '粉丝', '胡哨', '母', '数据', '刻意', '直接', '反复',
        # 扩充非人名词（R11 误报修复）
        '构建', '关键', '日常', '并尝试', '比喻', '这既', '这么',
        '现在', '以后', '然后', '接着', '最后', '首先', '其次',
        '所谓', '这个', '那个', '一个', '这种', '那种', '这些',
        '博弈', '缠斗', '观察', '信号', '泡沫', '解构', '逻辑',
        '当面临', '不需要', '不是', '在于', '不会',
        '有观点', '的说法', '王骁', '比喻', '比如', '其实', '所以',
        '有学者', '产出', '写出', '具体', '每周', '每月', '每天',
        '现场', '让她', '让我', '他说', '她说', '能随口', '能不',
    }

    for match in quote_attribution_pattern.finditer(note_text):
        person = match.group(1)
        line_num = note_text[:match.start()].count('\n') + 1

        # 跳过常见非人名词（精确匹配或前缀匹配）
        if person in non_person_words or any(person.startswith(w) for w in non_person_words if len(w) >= 2):
            continue
        # 去除尾部语气词/助词后再检查（如"翟东升也"→"翟东升"）
        person_stripped = re.sub(r'[也的了着过吗呢吧啊哦嘛]$', '', person)
        if person_stripped != person and person_stripped in source_text:
            continue

        # 检查这个人名是否在原文中出现过
        # 支持 ASR 常见的同音/形近字替换（如 翟→狄）
        name_in_source = person in source_text
        if not name_in_source:
            # 尝试同音/形近字模糊匹配（常见 ASR 误识别对）
            fuzzy_pairs = {
                '翟': '狄', '狄': '翟',
                '杨': '扬', '扬': '杨',
                '刘': '留', '留': '刘',
                '张': '章', '章': '张',
            }
            for src_char, tgt_char in fuzzy_pairs.items():
                if src_char in person:
                    fuzzy_name = person.replace(src_char, tgt_char)
                    if fuzzy_name in source_text:
                        name_in_source = True
                        break

        if not name_in_source:
            issues.append(Issue(
                rule_id="R11",
                rule_name="引用归属",
                severity="major",
                line_range=f"L{line_num}",
                description=f"引用归属存疑: '{person}'在原文中未出现，可能存在张冠李戴",
                suggestion=f"请核实'{person}'是否为正确的发言者；如为ASR误识别，请标注"
            ))

    score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.2)
    return RuleResult("R11", "引用归属", score, len(issues) == 0, issues)
