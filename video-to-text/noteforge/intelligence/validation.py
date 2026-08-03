# -*- coding: utf-8 -*-
"""
NoteForge 知识合成质量验证

从 SynthesisEngine 提取的合成质量验证逻辑，独立为可测试的模块级函数。

职责：
  - 来源标注真实性检查（验证「第X集」引用是否对应真实存在的笔记）
  - 交叉引用集数一致性检查
  - 思考过程/提示词泄漏检测（模型把规划过程输出进了合成文档）
  - 结构完整性检查（必需章节是否存在）
  - 来源覆盖度检查（合成是否覆盖了足够比例的源笔记）

严重度约定：
  - [严重]：输出结构/内容有实质问题，应触发重试（如 COT 泄漏、缺必需章节、来源全部失配）
  - [警告]：质量偏低但可通过（如覆盖度不足、个别来源标注缺失）

注意：不检查 prompt 要求 LLM 生成的章节名/标题数/金句数等可自满足项
（那些是 prompt 遵从性检查，不是知识质量检查，LLM 可轻易满足）。
"""

import re
from collections import Counter
from pathlib import Path
from typing import List, Optional, Tuple

# ------------------------------------------------------------
# 思考过程 / 提示词泄漏标记
#
# 模型在输出前如果做了规划（"先理清楚…"、"然后…"、"哦对…"），或把
# prompt 指令回显进文档（"你的任务是…"、"请根据以下…"），说明输出里混入了
# 思考过程而非最终文档。这些标记任一出现 ≥2 个，或文档首行即规划语言，判为泄漏。
# ------------------------------------------------------------
_COT_LEAK_MARKERS = [
    "用户现在需要我",     # 复述用户指令（规划起点）
    "现在开始组织内容",    # 过程规划
    "首先，先",           # 规划语言
    "哦对，",             # 自问自答（思考痕迹）
    "等下，再",           # 自问自答
    "再仔细看",           # 自问自答
    "你的任务是",         # 提示词回显
    "请根据以下",         # 提示词回显
    "逐集概念提取结果",    # 提示词结构回显
    "矛盾检测结果",        # 提示词结构回显
    # 英文思考痕迹（模型在中文输出前用英文做规划时，同样判定为泄漏）
    "The user wants me",   # 复述任务起点
    "Let me organize",     # 过程规划
    "Let me identify",     # 过程规划
    "Let me write",        # 过程规划
    "Let me draft",        # 过程规划
    "Now let me",          # 过程规划
    "Now let's",           # 过程规划
    "Now I will",          # 过程规划
    "I need to",           # 过程规划
    "I will now",          # 过程规划
    "Based on the",        # 复述输入
    "The input is",        # 复述输入
    "Here's my plan",      # 规划语言
    "Here is my plan",     # 规划语言
]

# 首行规划语言（文档正文第一行即以规划开头 → 强泄漏信号）
_COT_START_MARKERS = (
    "用户现在需要我", "现在需要我", "现在开始组织内容",
    "The user wants me", "the user wants me", "Let me", "let me",
    "Now let me", "Now I will", "I need to", "Let's ", "let's ",
    "First,", "Here's my", "Here is my", "I will now", "Based on the",
)

# 必需章节（对应 merge prompt 的输出结构；观点张力与矛盾仅在发现矛盾时可选）
# 分两级：
#   _SEVERE_SECTIONS：缺失判 [严重]（核心内容缺失，应重试）
#   _WARN_SECTIONS：缺失判 [警告]（可接受但输出不完整，降级保留）
_SEVERE_SECTIONS = [
    ("核心思维模型", ("核心思维模型", "思维模型")),
    ("方法论框架", ("方法论框架", "方法论")),
    ("学习路径", ("学习路径",)),
    ("金句精选", ("金句精选", "金句")),
]
_WARN_SECTIONS = [
    ("课程逻辑总览", ("课程逻辑总览", "逻辑总览")),
    ("跨集知识关联图", ("跨集知识关联图", "知识关联")),
    ("行动手册", ("行动手册",)),
    ("方法论速查表", ("方法论速查表", "速查表")),
]


def is_critical_issue(issue: str) -> bool:
    """判断问题是否严重（应触发重试）。"""
    return issue.startswith('[严重]')


def detect_cot_leak(text: str) -> List[str]:
    """检测思考过程/提示词泄漏，返回命中的标记列表（空列表 = 无泄漏）。

    供 Stage-1 逐集提取、增量提取等短输出做 COT 自检（合并输出走 validate_synthesis）。
    """
    return _detect_cot_leak(text)


def validate_synthesis(synthesis_text: str, note_paths: List[str]) -> List[str]:
    """验证合成文档质量，返回问题列表（空列表 = 通过）。

    检查维度：
      1. 来源标注真实性（「第X集」引用是否存在对应笔记）
      2. 交叉引用集数一致性
      3. 思考过程/提示词泄漏（COT）
      4. 结构完整性（必需章节）
      5. 来源覆盖度（引用源笔记的比例）
    """
    issues: List[str] = []

    # 源笔记是否有集数编号（第X集/epXX）——决定来源用「集数」还是「标题」核对
    numbered_notes = [p for p in note_paths if _has_episode_number(p)]
    available_eps = {_episode_number(p) for p in numbered_notes}
    available_eps = {n for n in available_eps if n}

    # 1. 来源标注检查 — 验证来源引用是否对应真实存在的笔记
    #    - 源笔记带集数：严格核对「第X集」引用（前导零归一化，第01集==第1集）
    #    - 源笔记按标题命名：不允许凭空引用「第X集」（应引用标题，由覆盖度检查兜底）
    ep_refs = re.findall(r'第\s*(\d+)\s*集', synthesis_text)
    if numbered_notes:
        if not ep_refs:
            issues.append("[警告] 未找到任何「第X集」来源标注（源笔记带集数编号）")
        else:
            norm_refs = {str(int(n)) for n in ep_refs}
            missing_eps = sorted(
                norm_refs - {str(int(n)) for n in available_eps}, key=int
            )
            if missing_eps:
                issues.append(
                    "[严重] 引用了不存在的集数: " + ', '.join(missing_eps)
                    + (f"（实际集数: {sorted((str(int(n)) for n in available_eps), key=int)}）"
                       if available_eps else "")
                )
    elif ep_refs:
        issues.append(
            "[警告] 文档使用「第X集」来源标注，但源笔记无集数编号"
            "（应按源笔记标题标注，如《互联网泡沫、量化崛起…》）"
        )

    # 2. 交叉引用集数一致性 — 仅对带集数编号的源笔记生效
    if numbered_notes:
        cross_ep_refs = re.findall(r'(?:前|后|上|下)\s*(\d+)\s*集', synthesis_text)
        if cross_ep_refs:
            missing_cross = sorted(
                {str(int(n)) for n in cross_ep_refs}
                - {str(int(n)) for n in available_eps}, key=int
            )
            if missing_cross:
                issues.append(
                    "[严重] 跨集引用了不存在的集数: " + ', '.join(missing_cross)
                )

    # 3. 思考过程 / 提示词泄漏检测
    cot_hits = _detect_cot_leak(synthesis_text)
    if cot_hits:
        issues.append(
            "[严重] 检测到思考过程/提示词泄漏: " + '、'.join(cot_hits[:5])
            + "（输出中混入了模型规划或指令回显，请重新生成纯文档正文）"
        )

    # 4. 结构完整性检查
    missing_severe = [n for n, alts in _SEVERE_SECTIONS
                      if not any(a in synthesis_text for a in alts)]
    if missing_severe:
        issues.append("[严重] 缺少核心章节: " + '、'.join(missing_severe))
    missing_warn = [n for n, alts in _WARN_SECTIONS
                    if not any(a in synthesis_text for a in alts)]
    if missing_warn:
        issues.append("[警告] 缺少可选章节: " + '、'.join(missing_warn))

    # 4b. 文档重复检测 — 同一章节标题出现 ≥2 次，说明输出拼接了草稿/多份文档
    dup_heads = _find_duplicate_section_heads(synthesis_text)
    if dup_heads:
        issues.append(
            "[严重] 检测到重复章节标题（输出疑似拼接草稿或多份文档）: "
            + '、'.join(dup_heads[:5])
        )

    # 5. 来源覆盖度检查
    referenced, total = _coverage_ratio(synthesis_text, note_paths)
    if total >= 3 and referenced == 0:
        issues.append(f"[严重] 未引用任何源笔记（0/{total}），疑似输出跑题或为空")
    elif total >= 3 and referenced / total < 0.5:
        issues.append(f"[警告] 来源覆盖度不足 ({referenced}/{total} < 50%)，多数源笔记未被引用")

    return issues


# ------------------------------------------------------------
# 内部检查实现
# ------------------------------------------------------------

def _detect_cot_leak(text: str) -> List[str]:
    """检测思考过程/提示词泄漏，返回命中的标记列表（空列表 = 无泄漏）。"""
    hits = [m for m in _COT_LEAK_MARKERS if m in text]
    # 至少命中 2 个不同标记，或正文首行即以规划语言开头 → 判定泄漏
    if len(hits) >= 2:
        return hits
    if _starts_with_cot(text):
        return ["首行为规划语言"]
    return []


def _starts_with_cot(text: str) -> bool:
    """检查正文第一行（非标题/引用/列表）是否以规划语言开头。"""
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(('#', '>', '-', '|', '*', '`')):
            continue
        return any(s.startswith(m) for m in _COT_START_MARKERS)
    return False


def _find_duplicate_section_heads(text: str) -> List[str]:
    """找出出现 ≥2 次的章节标题（## / ### 行），用于检测草稿拼接/文档重复。"""
    heads = re.findall(r'^#{1,2}\s+(.+?)\s*$', text, re.MULTILINE)
    counts = Counter(h.strip() for h in heads)
    return [h for h, n in counts.items() if n >= 2]


def _has_episode_number(path: str) -> bool:
    """源笔记文件名是否带集数编号（第X集 或 epXX）。"""
    stem = Path(path).stem
    return bool(re.search(r'第\s*\d+\s*集', stem)
                or re.search(r'ep\d+', stem, re.IGNORECASE))


def _episode_number(path: str) -> Optional[str]:
    """提取笔记文件名的集数编号（第X集或 epXX 的裸数字），无编号返回 None。"""
    stem = Path(path).stem
    m = re.search(r'第\s*(\d+)\s*集', stem)
    if m:
        return m.group(1)
    m = re.search(r'ep(\d+)', stem, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _strip_punct(s: str) -> str:
    """去除标点与空白，用于全标题匹配（兼容《…》引用与部分省略）。"""
    return re.sub(
        r'[\s，,。、：:；;！!？?“”"‘’\'《》〈〉…—\-()（）\[\]【】]', '', s
    )


def _coverage_ratio(text: str, note_paths: List[str]) -> Tuple[int, int]:
    """统计合成文档引用到的源笔记数量。

    匹配键取：
      - 「第X集/epXX」编号（源笔记带集数时）
      - 标题前段（如「互联网泡沫、量化崛起…」→「互联网泡沫」）
      - 去标点的完整标题（兼容《全标题》引用）
    """
    norm_text = _strip_punct(text)
    referenced = 0
    total = 0
    for p in note_paths:
        stem = Path(p).stem
        keys = []
        num = _episode_number(p)
        if num:
            keys.extend([f"第{num}集", f"第{int(num):02d}集", f"ep{num}"])
        head = re.split(r'[，,、：:。\s]', stem)[0]
        if len(head) >= 4:
            keys.append(head)
        norm_stem = _strip_punct(stem)
        if len(norm_stem) >= 8:
            keys.append(norm_stem)
        total += 1
        if any(k and (k in text or (len(k) >= 8 and k in norm_text)) for k in keys):
            referenced += 1
    return referenced, total
