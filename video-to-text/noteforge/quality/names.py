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

# 家庭/角色称谓同义词组（P2: R11 同义词感知，2026-08-09 实测）
# 背景：访谈类笔记常将转写中的口语称谓（"夫人太太"）改写为书面语（"妻子"），
# 精确串匹配会把这种同义改写误判为"张冠李戴"（major 误报）。
# 组内任一成员在原文出现，即视为该称谓归属成立（非虚构）。
# 维护原则：只收录语义无歧义的同义称谓，避免过宽分组引入"虚构也通过"的风险。
SYNONYM_GROUPS = [
    {'妻子', '夫人', '太太', '老婆', '爱人'},      # 配偶（女）
    {'丈夫', '老公', '夫君'},                       # 配偶（男）
    {'儿子', '女儿', '孩子', '子女'},               # 子女
    {'父亲', '爸爸', '老爹'},                       # 父
    {'母亲', '妈妈', '老娘'},                       # 母
    {'同事', '下属', '同僚'},                       # 共事关系
    {'领导', '老板', '上司', '主管'},               # 上级
]


def synonym_match_name(name: str, source_text: str) -> str:
    """查找 name 所属同义词组中、出现在原文的成员（返回原文成员，无则空串）。

    Args:
        name: 笔记中的人名/称谓
        source_text: 转写原文

    Returns:
        原文中出现的同义词成员；未命中返回 ''
    """
    for group in SYNONYM_GROUPS:
        if name in group:
            for member in group:
                if member != name and member in source_text:
                    return member
    return ''

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
    对人名做 ASR 同音/形近字 + 同义词模糊匹配。

    P2: 增加同义词组匹配（家庭/角色称谓），解决"妻子" vs 原文"夫人太太"
    的同义改写误报（2026-08-09 实测）。

    Returns:
        (是否匹配, 匹配到的原文人名或 None)
    """
    if name in source_text:
        return True, name
    # P2: 同义词组匹配（如 妻子=夫人=太太）
    synonym = synonym_match_name(name, source_text)
    if synonym:
        return True, synonym
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
