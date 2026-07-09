"""
feishu_blocks.py — Markdown → 飞书 Block 转换

负责将 Markdown 文本解析为飞书文档 API 的 Block 列表格式。

导入路径：
  from noteforge.integration.feishu_blocks import md_to_blocks

也可通过：
  from noteforge.integration.feishu import md_to_blocks
"""

import re
from typing import Optional

TEXT_RUN_MAX_LEN = 1500  # 单个 text_run 最大长度


def _split_text_run(content: str, style: Optional[dict] = None) -> list[dict]:
    """将长文本拆分为多个 text_run，每个不超过 TEXT_RUN_MAX_LEN。"""
    if not content:
        return []
    # 清理可能导致问题的特殊字符
    content = content.replace('•', '•').replace('‣', '•').replace('▪', '▪').replace('▫', '▫')
    style = style or {}
    runs: list[dict] = []
    while content:
        chunk = content[:TEXT_RUN_MAX_LEN]
        content = content[TEXT_RUN_MAX_LEN:]
        run: dict = {"text_run": {"content": chunk, "text_element_style": style}}
        runs.append(run)
    return runs


def _parse_inline(text: str) -> list[dict]:
    """解析行内格式：**粗体**、*斜体*、`代码`、~~删除线~~。"""
    elements: list[dict] = []
    pattern = re.compile(
        r'(\*\*(.+?)\*\*)'
        r'|(\*(.+?)\*)'
        r'|(`(.+?)`)'
        r'|(~~(.+?)~~)'
    )
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            elements.extend(_split_text_run(text[pos:m.start()]))
        if m.group(2) is not None:
            elements.extend(_split_text_run(m.group(2), {"bold": True}))
        elif m.group(4) is not None:
            elements.extend(_split_text_run(m.group(4), {"italic": True}))
        elif m.group(6) is not None:
            elements.extend(_split_text_run(m.group(6), {"inline_code": True}))
        elif m.group(8) is not None:
            elements.extend(_split_text_run(m.group(8), {"strikethrough": True}))
        pos = m.end()
    if pos < len(text):
        elements.extend(_split_text_run(text[pos:]))
    if not elements:
        elements.extend(_split_text_run(text))
    return elements


def _make_text_block(block_type: int, type_key: str, text: str, style: Optional[dict] = None) -> dict:
    """构造一个文本类 block。"""
    elements = _parse_inline(text)
    block: dict = {
        "block_type": block_type,
        type_key: {
            "elements": elements,
            "style": style if style is not None else {}
        },
    }
    return block


def md_to_blocks(md_content: str) -> list[dict]:
    """将 Markdown 文本转换为飞书 block 列表。"""
    blocks: list[dict] = []
    lines = md_content.split("\n")
    i = 0
    in_code_block = False
    code_lines: list[str] = []

    while i < len(lines):
        line = lines[i]

        if in_code_block:
            if line.strip().startswith("```"):
                # code block 在飞书 API 中有兼容问题，转为普通文本（带缩进标识）
                code_content = "\n".join(code_lines)
                blocks.append(_make_text_block(2, "text", f"```\n{code_content}\n```"))
                in_code_block = False
                code_lines = []
                i += 1
                continue
            code_lines.append(line)
            i += 1
            continue

        if line.strip().startswith("```"):
            in_code_block = True
            code_lines = []
            i += 1
            continue

        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if re.match(r'^-{3,}\s*$', stripped) or re.match(r'^\*{3,}\s*$', stripped):
            i += 1
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            if re.match(r'^\|[\s\-:]+\|$', stripped):
                i += 1
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            blocks.append(_make_text_block(2, "text", " | ".join(cells)))
            i += 1
            continue

        heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if heading_match:
            level = len(heading_match.group(1))
            blocks.append(_make_text_block(2 + level, f"heading{level}", heading_match.group(2).strip()))
            i += 1
            continue

        bullet_match = re.match(r'^[-*]\s+(.+)$', stripped)
        if bullet_match:
            # list block 在飞书 API 中有兼容问题，转为普通文本
            blocks.append(_make_text_block(2, "text", f"• {bullet_match.group(1)}"))
            i += 1
            continue

        ordered_match = re.match(r'^\d+\.\s+(.+)$', stripped)
        if ordered_match:
            # ordered list block 在飞书 API 中有兼容问题，转为普通文本
            blocks.append(_make_text_block(2, "text", f"{ordered_match.group(0)}"))
            i += 1
            continue

        quote_match = re.match(r'^>\s?(.*)$', stripped)
        if quote_match:
            if quote_match.group(1):
                # quote_container 是容器类型，不能直接写入文本，转为普通文本
                blocks.append(_make_text_block(2, "text", f"> {quote_match.group(1)}"))
            i += 1
            continue

        blocks.append(_make_text_block(2, "text", stripped))
        i += 1

    return blocks
