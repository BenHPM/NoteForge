# -*- coding: utf-8 -*-
"""
NoteForge 人名/引用提取共享模块

R11（引用归属）和 R12（人名/数字一致性）共用：
- 人名提取正则（X说/认为/指出/表示 模式）
- 非人名词排除集合（NON_PERSON_WORDS）
- ASR 同音/形近字模糊匹配对（FUZZY_PAYS）

单一维护点：修改排除列表或模糊对只在此处编辑。
"""

import re
from typing import List, Tuple


# 常见非人名词（排除匹配）
# 维护原则：只添加 2-4 字中文词组，且该词组 + 说/认为/指出 等动词
# 在笔记语境中不构成人名引用（如"核心观点"不是"核心说"）
# 新增领域术语时同步更新此列表
NON_PERSON_WORDS = {
    # 通用抽象词
    '原文', '笔记', '总结', '分析', '框架', '方法', '观点', '理论',
    '模型', '策略', '核心', '关键', '重要', '数据', '逻辑',
    # 指代词/连词
    '因此', '所以', '但是', '然而', '虽然', '如果', '因为', '通过',
    '大家', '我们', '他们', '你们', '自己', '什么', '这个', '那个',
    '所谓', '一个', '这种', '那种', '这些', '其实', '用户',
    # 序列/结构词
    '第一', '第二', '第三', '第四', '第五', '第六', '第七', '第八',
    '一步', '句话', '方面', '层面', '角度', '维度', '阶段', '环节',
    '首先', '其次', '最后', '然后', '接着', '以后', '现在',
    # 常见误匹配动词/副词
    '刻意', '直接', '反复', '构建', '日常', '具体', '产出',
    '写出', '观察', '解构', '不需要', '不是', '在于', '不会',
    # 角色称呼（非具体人名）
    '讲师', '老师', '嘉宾', '主持人', '有学者',
    # 短视频/内容创作领域
    '片子', '粉丝', '胡哨', '比喻', '比如',
    # 金融/投资领域
    '博弈', '缠斗', '信号', '泡沫',
    # 时间频率词
    '每周', '每月', '每天',
    # 其他已知误匹配
    '母', '这既', '这么', '并尝试', '的说法', '有观点',
    '现场', '让她', '让我', '他说', '她说', '能随口', '能不',
    '可能被', '问题明确', '我可以', '这既', '根据',
    # 2026-07 补充：更多常见误匹配
    '结构性', '系统性', '基本面', '宏观', '微观', '长期',
    '短期', '整体', '局部', '绝对', '相对', '必然', '偶然',
    '王骁',  # 特定人名但非引用对象（"王骁说"通常是内容标题而非引用）
}

# ASR 常见同音/形近字模糊匹配对（如 翟→狄）
FUZZY_PAIRS = {
    '翟': '狄', '狄': '翟',
    '杨': '扬', '扬': '杨',
    '刘': '留', '留': '刘',
    '张': '章', '章': '张',
}

# 人名提取正则：人名（2-4 中文字）+ 说/认为/指出/表示...
# 人名限制：必须在句首/标点后/空格后（避免误匹配词组中间）
# 排除前导连词（但/而/且/又/就/也/还/都/却）
_PERSON_ATTRIBUTION_RE = re.compile(
    r'(?:^|[\s，。、；：！？\n>|*「」""\-\[])(?:[但而且就也还都却])?'
    r'([一-鿿]{2,4})\s*'
    r'(?:说|认为|指出|表示|提到|强调|主张|分析|解释|总结道)[：:]?\s*',
    re.MULTILINE,
)


def extract_person_attributions(text: str) -> List[Tuple[str, int]]:
    """
    从文本中提取「X说/认为/指出」模式的人名及其行号。

    已应用非人名词排除 + 去重。返回 [(人名, 行号), ...]。
    R11 和 R12 共用此函数，差异仅在于后续如何校验人名。
    """
    results: List[Tuple[str, int]] = []
    seen: set = set()
    for match in _PERSON_ATTRIBUTION_RE.finditer(text):
        person = match.group(1)
        if person in NON_PERSON_WORDS:
            continue
        # 前缀匹配排除（如 person 以某个非人名词开头）
        if any(person.startswith(w) for w in NON_PERSON_WORDS if len(w) >= 2):
            continue
        if person in seen:
            continue
        seen.add(person)
        line_num = text[:match.start()].count('\n') + 1
        results.append((person, line_num))
    return results


def fuzzy_match_name(name: str, source_text: str) -> Tuple[bool, str]:
    """
    对人名做 ASR 同音/形近字模糊匹配。

    Returns:
        (是否匹配, 匹配到的原文人名或 None)
    """
    if name in source_text:
        return True, name
    for src_c, tgt_c in FUZZY_PAIRS.items():
        if src_c in name:
            candidate = name.replace(src_c, tgt_c)
            if candidate in source_text:
                return True, candidate
    # 去除尾部语气词再试
    stripped = re.sub(r'[也的了着过吗呢吧啊哦嘛]$', '', name)
    if stripped != name and stripped in source_text:
        return True, stripped
    return False, None
