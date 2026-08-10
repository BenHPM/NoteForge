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
    # 2026-08-10 6h 访谈实测新增：引用碎片被误当人名
    '前面', '当时', '担心', '要求', '说法', '原话', '自己',
    '怕人',  # "怕人说"（担心别人说）被当人名
}

# 强非人名子串（出现在名字任意位置即判定为非人名）
# 与 startswith 集合（NON_PERSON_WORDS）不同：这些词不可能作为真实姓名的组成部分。
# 2026-08-10 6h 访谈 run2 实测新增：贪婪正则截取的句子碎片——
#   '谢明确说'（明确是谢与说之间的副词）→ 提取 '谢明确' 需按子串剔除 '明确'
#   '人名校对说明'（自检标题）→ 剔除 '人名'
#   '悲观的人'（'X的人' 名词短语）→ 剔除 '的人'（的 在中间，尾部黑名单拦不到）
# 维护原则：只收不可能出现在人名内的词。切勿把 '老师/教授' 之类可作称谓后缀的词
# 放入此集合——否则会误杀 '于老师/马毅老师/沈教授' 等真实称谓。
_FRAGMENT_SUBSTR = frozenset({
    '原文', '明确', '人名', '前面', '当时', '担心', '要求',
    '说法', '原话', '自己', '的人',
})

# 名字尾字符黑名单：真实人名不会以这些字符结尾（功能字/虚词/量词）。
# 用于拦截 '用原文的'（尾"的"）、'莱姆小'（"莱姆小说"的 说 被 小说 吞并）、
# '敲定后才'（"敲定后才说"——"才"是副词）等碎片。
# 维护原则：只收几乎不可能作人名单名末字的字符。
_NAME_ENDING_BLACKLIST = frozenset(
    '的说话法们小的了着过吧啊呢哦才'
)

# ASR 常见同音/形近字模糊匹配对（如 翟→狄）
# 2026-08-10 run2 新增 柯→科：贾樟柯（真名）被 ASR 转写为 贾樟科（柯/科同音）。
FUZZY_PAIRS = {
    '翟': '狄', '狄': '翟',
    '杨': '扬', '扬': '杨',
    '刘': '留', '留': '刘',
    '张': '章', '章': '张',
    '柯': '科', '科': '柯',
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
# group(2)=动词（"X自认为"模式需要判断动词来剥离尾"自"）
_PERSON_ATTRIBUTION_RE = re.compile(
    r'(?:^|[\s，。、；：！？\n>|*「」""\-\[])(?:[但而且就也还都却])?'
    r'([一-鿿]{2,4})\s*'
    r'(说|认为|指出|表示|提到|强调|主张|分析|解释|总结道)[：:]?\s*',
    re.MULTILINE,
)

# 前导字符黑名单：代词/系词/否定/方位等不能作为中文人名首字。
# 用于拦截"他前面/当时他/是要求/不用担心"这类被贪婪正则截取的句子碎片。
# 2026-08-10 run2 追加 '可'（"可被事实"——"可以被"被当人名）。
# 维护原则：只收不可能作姓氏或人名的功能字（勿放 于/何/和/向/那 等罕见姓）。
_LEADING_NON_NAME = frozenset(
    '他她它我你当是用不这那都就还也很更最但而且又只才并却再'
    '上中下前后左右从被让把对往每各某今昨明该正刚自可'
)


def is_plausible_person_name(person: str, verb: str = '') -> str:
    """结构合理性过滤：判断贪婪正则捕获的 2-4 字是否为真实人名。

    返回净化后的名字（可能剥离尾"自"），非人名返回 ''。
    R11/R12 的 extract_person_attributions 共用，单一维护点。

    2026-08-10 6h 访谈实测（run1 + run2 两轮碎片风暴）：
      1. "X自认为" → 剥离尾"自"（谢赛宁自认为 → 谢赛宁）
      2. startswith 非人名词（"用原文的"/"是原文" 等句首碎片）
      3. 任意位置含强非人名子串（"谢明确说"中的 明确、"人名校对"中的 人名）
      4. 首字为代词/系词/否定/方位功能字（"他前面/当时他/是要求/不用担心"）
      5. 尾字符黑名单（"用原文的"尾"的"、"莱姆小说"吞并说 → "莱姆小"尾"小"）
    """
    # "X自认为/自以为是" → 剥离尾"自"（自然语言自指，非人名一部分）
    if person.endswith('自') and verb in ('认为', '以为'):
        person = person[:-1]
    # "谢赛宁不认为" → 剥离夹在名字与动词间的否定词"不"（"不"不可能作人名末字）
    elif person.endswith('不') and len(person) >= 3:
        person = person[:-1]
    if len(person) < 2:
        return ''
    if person in NON_PERSON_WORDS:
        return ''
    # 句首碎片（startswith 全列表，含 '于老师'→不以 '老师' 开头，安全）
    if any(person.startswith(w) for w in NON_PERSON_WORDS if len(w) >= 2):
        return ''
    # 任意位置含强非人名子串（针对性集合，勿用全列表——会误杀 于老师/沈教授）
    if any(w in person for w in _FRAGMENT_SUBSTR):
        return ''
    # 首字为不可能作人名首字的功能字（代词/系词/否定/方位等）
    if person[0] in _LEADING_NON_NAME:
        return ''
    # 尾字符黑名单（功能字/虚词/量词结尾）
    if person[-1] in _NAME_ENDING_BLACKLIST:
        return ''
    return person


def extract_person_attributions(text: str) -> List[Tuple[str, int]]:
    """
    从文本中提取「X说/认为/指出」模式的人名及其行号。

    已应用非人名词排除 + 结构合理性过滤 + 去重。返回 [(人名, 行号), ...]。
    R11 和 R12 共用此函数，差异仅在于后续如何校验人名。
    """
    results: List[Tuple[str, int]] = []
    seen: set = set()
    for match in _PERSON_ATTRIBUTION_RE.finditer(text):
        person = is_plausible_person_name(match.group(1), match.group(2))
        if not person:
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
