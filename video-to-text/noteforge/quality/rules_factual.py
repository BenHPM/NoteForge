# -*- coding: utf-8 -*-
"""
NoteForge 事实性质量规则（R1, R2, R3, R12）

与事实正确性相关的规则检查函数：
- R1: 禁止虚构数据
- R2: 禁止越界增补
- R3: 禁止事实反转
- R12: 人名/数字一致性

辅助函数：
- num_to_chinese: 数字转中文
- number_in_source: 智能数字匹配
"""

import re
import logging
from noteforge.quality.models import Issue, RuleResult

logger = logging.getLogger('noteforge.quality')


# ----------------------------------------------------------
# 辅助函数
# ----------------------------------------------------------

# 数字 → 中文映射（0-100 + 整十）
_DIGIT_TO_CN = {
    '0': '零', '1': '一', '2': '二', '3': '三', '4': '四',
    '5': '五', '6': '六', '7': '七', '8': '八', '9': '九',
    '10': '十', '20': '二十', '30': '三十', '40': '四十',
    '50': '五十', '60': '六十', '70': '七十', '80': '八十',
    '90': '九十', '100': '一百',
}


def num_to_chinese(num_str: str) -> str:
    """将数字字符串转为中文（支持 0-999 的整数和一位小数）"""
    try:
        val = float(num_str)
    except ValueError:
        return ""
    # 整数
    if val == int(val):
        n = int(val)
        if 0 <= n <= 100:
            if n <= 10:
                return _DIGIT_TO_CN.get(str(n), '')
            if n % 10 == 0:
                return _DIGIT_TO_CN.get(str(n), '')
            tens, ones = divmod(n, 10)
            t = _DIGIT_TO_CN.get(str(tens * 10), '')
            o = _DIGIT_TO_CN.get(str(ones), '')
            return t + o
        return str(n)  # 超过100不转换
    # 一位小数
    parts = num_str.split('.')
    if len(parts) == 2 and len(parts[1]) <= 1:
        int_part = num_to_chinese(parts[0])
        dec_part = _DIGIT_TO_CN.get(parts[1], '')
        return f"{int_part}点{dec_part}" if int_part else ""
    return ""


def number_in_source(num_expr: str, source_text: str) -> bool:
    """智能数字匹配：检查数字表达式是否在原文中有对应

    支持的匹配模式：
    - 精确匹配: "25%" in source
    - 去空格: "25%" vs "25 %"
    - 百分之X: "25%" vs "百分之二十五"
    - 近似前缀: "约25%" vs "25%"
    - 纯数字: "25" in "百分之二十五"
    - 中文数字: "25%" vs "二十五个百分点"
    """
    # 1. 精确匹配
    if num_expr in source_text:
        return True

    # 提取数值部分
    num_match = re.search(r'[\d.]+', num_expr)
    if not num_match:
        return False
    num_val = num_match.group()

    # 2. 去空格变体（"25 %" ↔ "25%"）
    no_space = num_val + '%'
    if no_space in source_text:
        return True
    with_space = num_val + ' %'
    if with_space in source_text:
        return True

    # 3. "百分之X" 格式
    cn_num = num_to_chinese(num_val)
    if cn_num:
        pct_cn = f"百分之{cn_num}"
        if pct_cn in source_text:
            return True
        # 4. "X个百分点" / "X个点"
        if f"{cn_num}个百分点" in source_text:
            return True
        if f"{cn_num}个点" in source_text:
            return True
        # 5. 纯中文数字（原文可能没有"百分之"）
        if cn_num in source_text:
            return True

    # 6. 去除近似前缀后匹配
    stripped = re.sub(r'^[约近超大约不到接近]+', '', num_expr)
    if stripped != num_expr and stripped in source_text:
        return True

    # 7. 纯数字在原文中出现
    if num_val in source_text:
        return True

    return False


# ----------------------------------------------------------
# R1: 禁止虚构数据
# ----------------------------------------------------------
def check_fabricated_data(fabricated_patterns: list, note_text: str,
                          source_text: str) -> RuleResult:
    """扫描笔记中的数字/百分比，检查原文是否有出处"""
    issues = []

    for pattern in fabricated_patterns:
        for match in re.finditer(pattern, note_text):
            number_context = match.group()
            line_num = note_text[:match.start()].count('\n') + 1

            # 跳过时间戳格式（2:30, 5:50, 01:23:45 等）
            if re.match(r'^[\d.]+\s*[：:]\s*\d+', number_context):
                continue
            # 跳过 Markdown 引用中的数字
            ctx_start = max(0, match.start() - 20)
            ctx_prefix = note_text[ctx_start:match.start()]
            if ctx_prefix.rstrip().endswith('>'):
                continue

            # 在原文中查找该数字（智能匹配）
            if not number_in_source(number_context, source_text):
                issues.append(Issue(
                    rule_id="R1",
                    rule_name="禁止虚构数据",
                    severity="fatal",
                    line_range=f"L{line_num}",
                    description=f"疑似虚构数据: '{number_context}' 在原文中未找到出处",
                    suggestion="删除该数字或标注原文出处"
                ))

    score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.25)
    return RuleResult("R1", "禁止虚构数据", score, len(issues) == 0, issues)


# ----------------------------------------------------------
# R2: 禁止越界增补
# ----------------------------------------------------------
def check_unmarked_additions(note_text: str, source_text: str) -> RuleResult:
    """检测笔记中是否有未标注补充分割的内容"""
    issues = []

    # 检查是否有"[📝笔者补充]"标记
    has_mark = "[📝笔者补充]" in note_text or "[📝补充]" in note_text

    # 检测可能有增补嫌疑的段落（建议/策略类内容）
    suspicion_patterns = [
        (r'✅\s*(?:短期|中期|长期)\s*(?:应对|策略|建议).*?(?=\n\n|\Z)', '策略建议类内容'),
        (r'(?:📈|📊)\s*(?:短期|中期|长期)\s*(?:布局|规划|策略).*?(?=\n\n|\Z)', '策略规划类内容'),
        (r'(?:应对|行动|实施)\s*(?:策略|方案|建议)\s*(?:建议|清单|指南)', '策略建议标题'),
        (r'(?:综上|总之|总结).*(?:建议|推荐|应该)\s*(?:采取|执行|实施).*?(?=\n\n|\Z)', '总结性建议'),
    ]

    for pattern, desc in suspicion_patterns:
        for match in re.finditer(pattern, note_text, re.DOTALL):
            matched_text = match.group()
            if matched_text not in source_text:
                line_num = note_text[:match.start()].count('\n') + 1
                if not has_mark:
                    mark_label = "[📝笔者补充]"
                    issues.append(Issue(
                        rule_id="R2",
                        rule_name="禁止越界增补",
                        severity="fatal",
                        line_range=f"L{line_num}",
                        description=f"疑似越界增补({desc}): 此内容在原文中未找到，且无{mark_label}标记",
                        suggestion=f"如确为补充内容，请添加{mark_label}标记；如非必要，请删除"
                    ))

    score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.3)
    return RuleResult("R2", "禁止越界增补", score, len(issues) == 0, issues)


# ----------------------------------------------------------
# R3: 禁止事实反转
# ----------------------------------------------------------
def check_semantic_reversal(note_text: str, source_text: str) -> RuleResult:
    """检测可能的语义反转（半自动：标记可疑模式供人工复查）"""
    issues = []

    # 已知的历史反转模式
    # severity 区分：fatal=高置信反转 / medium=低置信疑似（需人工确认）
    # 每条格式: (笔记中的模式, 原文中应存在的对应模式, 严重度)
    reversal_patterns = [
        # --- 金融/投资领域 ---
        ("可解释性[强高好]", "不是黑箱|非黑箱|可解释性弱|不可解释", "fatal"),
        ("不是黑箱|非黑箱", "可解释性[强高好]", "fatal"),
        ("逆向思维", "基本面趋势|右侧投资", "medium"),
        ("量化优于.*主观", "不可解释.*因子|模型训练", "medium"),
        # 新增：金融/投资常见反转
        ("长期[看涨看好]?", "短期|回调|风险|下行|看空|看跌", "medium"),
        ("风险[很低较小]|低风险", "高风险|波动大|回撤|杠杆|爆仓", "fatal"),
        ("收益[稳定确定]|稳定收益", "不确定|波动|亏损|回撤|最大回撤", "medium"),
        ("分散.*降低风险|降低.*风险", "集中|重仓|单一|杠杆", "medium"),
        ("价值投资|长期持有", "短线|投机|追涨|快进快出|T\\+0", "medium"),
        # --- 地缘/政治领域 ---
        ("合作|互利|共赢", "对抗|制裁|脱钩|冲突|贸易战", "medium"),
        ("稳定|和平|缓和", "动荡|战争|紧张|升级|冲突", "medium"),
        ("全球化|一体化|融合", "逆全球化|脱钩|分裂|碎片化", "medium"),
        # --- 短视频/创作领域 ---
        ("原创|创新|独特", "抄袭|模仿|搬运|同质化", "medium"),
        ("自然增长|有机增长", "买量|刷量|投流|付费推广", "medium"),
    ]

    for note_pattern, expected_source_pattern, severity in reversal_patterns:
        if re.search(note_pattern, note_text):
            if not re.search(expected_source_pattern, source_text):
                for match in re.finditer(note_pattern, note_text):
                    line_num = note_text[:match.start()].count('\n') + 1
                    issues.append(Issue(
                        rule_id="R3",
                        rule_name="禁止事实反转",
                        severity=severity,
                        line_range=f"L{line_num}",
                        description=f"疑似语义反转: 笔记中出现'{match.group()}'，但原文中未找到对应表述",
                        suggestion="请人工核实该论点是否与原文语义方向一致"
                    ))

    score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.3)
    return RuleResult("R3", "禁止事实反转", score, len(issues) == 0, issues)


# ----------------------------------------------------------
# R12: 人名/数字一致性
# ----------------------------------------------------------
def check_name_number_consistency(note_text: str,
                                  source_text: str) -> RuleResult:
    """校验笔记中的人名和关键数字是否与转写原文一致"""
    issues = []

    # 1. 人名一致性：提取笔记中的「X说/认为/指出」模式，检查原文
    name_pattern = re.compile(
        r'(?:^|[\s，。；：\n])([一-鿿]{2,4})\s*(?:说|认为|指出|表示|提到|强调|解释|分析)',
        re.MULTILINE,
    )
    non_person = {
        '讲师', '老师', '嘉宾', '主持人', '原文', '笔记', '总结',
        '分析', '框架', '方法', '观点', '策略', '模型', '理论',
        '大家', '我们', '他们', '你们', '自己', '什么', '这个',
        '那个', '王骁', '有观点', '有学者', '比喻', '说法',
        '能随口', '能不', '现场', '让她', '让我', '他说', '她说',
        '关键', '核心', '重要', '构建', '日常', '比如', '其实',
    }

    checked_names = set()
    for match in name_pattern.finditer(note_text):
        name = match.group(1)
        if name in non_person or len(name) < 2:
            continue
        if name in checked_names:
            continue
        checked_names.add(name)

        # 检查原文
        if name not in source_text:
            # 同音/形近字模糊匹配
            fuzzy_pairs = {'翟': '狄', '狄': '翟', '杨': '扬', '扬': '杨'}
            fuzzy_name = None
            found = False
            for src_c, tgt_c in fuzzy_pairs.items():
                if src_c in name:
                    fuzzy_name = name.replace(src_c, tgt_c)
                    if fuzzy_name in source_text:
                        found = True
                        break
            if not found:
                fuzzy_name = None

            if not found:
                # 去除尾部语气词再试
                stripped = re.sub(r'[也的了着过吗呢吧啊哦嘛]$', '', name)
                if stripped != name and stripped in source_text:
                    found = True

            if found and fuzzy_name:
                # 同音/形近字匹配成功 — 标记为 ASR 容差
                line_num = note_text[:match.start()].count('\n') + 1
                issues.append(Issue(
                    rule_id="R12",
                    rule_name="人名/数字一致性",
                    severity="medium",
                    line_range=f"L{line_num}",
                    description=f"人名ASR差异: '{name}' vs 原文 '{fuzzy_name}'（ASR同音字差异）",
                    suggestion=f"可能是ASR转写差异，请核实'{name}'是否正确"
                ))
            elif not found:
                line_num = note_text[:match.start()].count('\n') + 1
                issues.append(Issue(
                    rule_id="R12",
                    rule_name="人名/数字一致性",
                    severity="medium",
                    line_range=f"L{line_num}",
                    description=f"人名'{name}'在转写原文中未找到对应，可能为误写或ASR差异",
                    suggestion=f"请核实'{name}'是否正确，或标注「原文此处不清晰」"
                ))

    # 2. 关键数字一致性：提取笔记中的百分比和大数字，检查原文
    number_pattern = re.compile(r'[\d.]+\s*%')
    for match in number_pattern.finditer(note_text):
        num = match.group()
        # 使用智能匹配（支持 "百分之X"、中文数字、近似表达等）
        if not number_in_source(num, source_text):
            line_num = note_text[:match.start()].count('\n') + 1
            issues.append(Issue(
                rule_id="R12",
                rule_name="人名/数字一致性",
                severity="medium",
                line_range=f"L{line_num}",
                description=f"数字'{num}'在转写原文中未找到对应",
                suggestion="请核实该数字是否有原文出处，或改为定性描述"
            ))

    score = 1.0 if len(issues) == 0 else max(0.0, 1.0 - len(issues) * 0.15)
    return RuleResult("R12", "人名/数字一致性", score, len(issues) == 0, issues)
