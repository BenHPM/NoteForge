"""
feishu_client.py — 飞书知识库 API 客户端（通过 lark-cli 调用）

功能：
  1. FeishuClient: 通过 lark-cli api 子进程调用飞书 API（用户身份，自动管理 token）
  2. md_to_blocks(): Markdown → 飞书 Block 转换
  3. match_category(): 按文件名匹配领域分类

用法：
  from feishu_client import FeishuClient, md_to_blocks, match_category
"""

import fnmatch
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger('noteforge.feishu')

# ============================================================
# 常量
# ============================================================

BLOCK_BATCH_SIZE = 50        # 每批写入 block 上限
TEXT_RUN_MAX_LEN = 1500      # 单个 text_run 最大长度


# ============================================================
# 飞书 API 客户端（通过 lark-cli）
# ============================================================

class FeishuClient:
    """飞书知识库 API 封装，通过 lark-cli api 子进程调用（用户身份）。"""

    # lark-cli 可执行文件路径（首次使用时自动查找）
    _lark_cli_path: Optional[str] = None

    def __init__(
        self,
        space_id: str,
        block_batch_size: int = BLOCK_BATCH_SIZE,
        dry_run: bool = False,
    ):
        self.space_id = space_id
        self.block_batch_size = block_batch_size
        self.dry_run = dry_run

        # 首次使用时查找 lark-cli
        if FeishuClient._lark_cli_path is None:
            FeishuClient._lark_cli_path = self._find_lark_cli()

    @staticmethod
    def _find_lark_cli() -> str:
        """查找 lark-cli 可执行文件。"""
        # 优先用 which/where 查找
        try:
            result = subprocess.run(
                ["where", "lark-cli"] if __import__('sys').platform == "win32" else ["which", "lark-cli"],
                capture_output=True, text=True, timeout=5, shell=True,
            )
            if result.returncode == 0:
                path = result.stdout.strip().split("\n")[0].strip()
                if path:
                    logger.debug(f"找到 lark-cli: {path}")
                    return path
        except Exception:
            pass
        # 回退到默认名
        return "lark-cli"

    # ------ 通用请求 ------

    def _api(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> dict:
        """通过 lark-cli api 调用飞书 API。"""
        cmd = [self._lark_cli_path, "api", "--as", "user", method, path]
        stdin_data = None
        tmp_file = None
        if params:
            cmd.extend(["--params", "-"])
            stdin_data = json.dumps(params, ensure_ascii=False)
        if data:
            # 写入临时文件到当前目录（lark-cli 要求相对路径）
            tmp_file = tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False, dir='.', encoding='utf-8'
            )
            tmp_file.write(json.dumps(data, ensure_ascii=False))
            tmp_file.close()
            tmp_rel = os.path.basename(tmp_file.name)
            cmd.extend(["--data", f"@{tmp_rel}"])
            # DEBUG: 打印实际发送的数据
            logger.debug(f"  [DEBUG] 发送数据: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}...")
        cmd.append("--json")

        if self.dry_run:
            logger.info(f"[dry-run] API {method} {path}")
            if tmp_file:
                os.unlink(tmp_file.name)
            return {"code": 0, "data": {}}

        print(f"  [DEBUG] API {method} {path} stdin={stdin_data is not None} tmpfile={tmp_file is not None}")
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60, shell=True,
                input=stdin_data,
            )
        finally:
            if tmp_file:
                try:
                    os.unlink(tmp_file.name)
                except Exception:
                    pass

        # lark-cli 成功时输出到 stdout，错误时输出到 stderr
        raw = result.stdout.strip() or result.stderr.strip()
        if not raw:
            raise RuntimeError(f"lark-cli 无输出 (exit={result.returncode})")
        try:
            resp = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"lark-cli 返回非 JSON (exit={result.returncode}):\n"
                f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
            )

        # 检查 lark-cli 级别错误
        if resp.get("ok") is False:
            err = resp.get("error", {})
            logger.error(f"[API ERROR] {err}")
            raise RuntimeError(
                f"lark-cli 错误: {err.get('message', '')} (type={err.get('type', '')})"
            )

        # 检查飞书 API 级别错误
        code = resp.get("code", -1)
        if code != 0:
            msg = resp.get("msg", "")
            logger.error(f"[API ERROR] code={code} msg={msg}")
            raise RuntimeError(f"飞书 API 错误 (code={code}): {msg}")

        return resp

    # ------ 知识库节点操作 ------

    def list_child_nodes(self, parent_node_token: str) -> list[dict]:
        """列出某节点的子节点。"""
        print(f"  [DEBUG] list_child_nodes parent={parent_node_token}")
        data = self._api(
            "GET",
            f"wiki/v2/spaces/{self.space_id}/nodes",
            params={"parent_node_token": parent_node_token, "page_size": 50},
        )
        return data.get("data", {}).get("items", [])

    def find_node_by_title(self, parent_node_token: str, title: str) -> Optional[dict]:
        """在子节点中按标题查找。"""
        if self.dry_run:
            logger.info(f"[dry-run] 检查节点是否存在: {title}")
            return None
        children = self.list_child_nodes(parent_node_token)
        for child in children:
            if child.get("title") == title:
                return child
        return None

    def create_node(self, parent_node_token: str, title: str, obj_type: str = "docx") -> dict:
        """创建知识库节点。返回节点信息（含 node_token 和 obj_token）。"""
        if self.dry_run:
            logger.info(f"[dry-run] 将创建 {obj_type} 节点: {title} (parent={parent_node_token})")
            return {"node_token": f"dry-run-{hash(title) % 100000}", "obj_type": obj_type, "title": title}

        existing = self.find_node_by_title(parent_node_token, title)
        if existing:
            logger.info(f"节点已存在: {title} (node_token={existing.get('node_token', '?')})")
            return existing

        data = self._api(
            "POST",
            f"wiki/v2/spaces/{self.space_id}/nodes",
            data={
                "obj_type": obj_type,
                "parent_node_token": parent_node_token,
                "node_type": "origin",
                "title": title,
            },
        )
        node = data.get("data", {}).get("node", {})
        logger.info(f"创建 {obj_type} 节点成功: {title} (node_token={node.get('node_token', '?')})")
        return node

    # ------ 文档内容操作 ------

    def get_document_blocks(self, document_id: str) -> list[dict]:
        """获取文档的 block 列表。"""
        all_blocks: list[dict] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            data = self._api("GET", f"docx/v1/documents/{document_id}/blocks", params=params)
            items = data.get("data", {}).get("items", [])
            all_blocks.extend(items)
            if not data.get("data", {}).get("has_more", False):
                break
            page_token = data.get("data", {}).get("page_token", "")
        return all_blocks

    def delete_block_children(self, document_id: str, block_id: str) -> None:
        """删除文档 block 的所有子 block（清空文档内容）。"""
        blocks = self.get_document_blocks(document_id)
        root_children = [
            b["block_id"] for b in blocks
            if b.get("parent_id") == block_id and b.get("block_id") != block_id
        ]
        if not root_children:
            return
        self._api(
            "DELETE",
            f"docx/v1/documents/{document_id}/blocks/{block_id}/children/batch_delete",
            data={"start_index": 0, "end_index": len(root_children)},
        )

    def overwrite_document(self, document_id: str, blocks: list[dict]) -> None:
        """清空文档内容后重写。"""
        if self.dry_run:
            logger.info(f"[dry-run] 将覆写文档 {document_id}（{len(blocks)} 个 block）")
            return
        logger.info(f"  清空文档 {document_id}...")
        self.delete_block_children(document_id, document_id)
        time.sleep(0.5)
        self.append_blocks(document_id, blocks)

    def append_blocks(self, document_id: str, blocks: list[dict]) -> None:
        """分批追加 blocks 到文档末尾。"""
        if self.dry_run:
            logger.info(f"[dry-run] 将向文档 {document_id} 写入 {len(blocks)} 个 block")
            return

        total = len(blocks)
        for i in range(0, total, self.block_batch_size):
            batch = blocks[i:i + self.block_batch_size]
            self._api(
                "POST",
                f"docx/v1/documents/{document_id}/blocks/{document_id}/children",
                data={"children": batch, "index": -1},
            )
            logger.info(f"  写入 block {i + 1}-{i + len(batch)}/{total}")

        if total > 0:
            time.sleep(0.5)

    def create_document_and_write(self, parent_node_token: str, title: str, blocks: list[dict]) -> Optional[str]:
        """创建文档节点 → 等待 → 写入内容。返回 obj_token。"""
        node = self.create_node(parent_node_token, title, obj_type="docx")
        # obj_token 用于文档内容操作，node_token 用于 wiki 节点操作
        obj_token = node.get("obj_token") or node.get("node_token", "")

        if not blocks:
            logger.warning(f"文档 {title} 无内容块，跳过写入")
            return obj_token

        if not self.dry_run:
            logger.info(f"  等待飞书内部同步...")
            time.sleep(1.5)

        self.append_blocks(obj_token, blocks)
        return obj_token

    def ensure_category_node(self, root_node_token: str, category_title: str) -> str:
        """确保分类节点存在，返回 node_token（用于 wiki 节点操作）。
        兼容旧节点：如果传入 "📁 跨集提炼" 但只找到 "跨集提炼"，也会返回。
        """
        node = self.create_node(root_node_token, category_title, obj_type="docx")
        return node.get("node_token", "")


# ============================================================
# Markdown → 飞书 Block 转换
# ============================================================

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


def yaml_to_doc_blocks(yaml_content: str, title: str) -> list[dict]:
    """将 YAML 配置文件格式化为说明文档的 block 列表。"""
    blocks: list[dict] = []
    blocks.append(_make_text_block(2, "text", f"以下为 {title} 的完整配置内容："))
    blocks.append(_make_text_block(2, "text", ""))

    chunks: list[str] = []
    lines = yaml_content.split("\n")
    current_chunk: list[str] = []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > TEXT_RUN_MAX_LEN and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_len = 0
        current_chunk.append(line)
        current_len += len(line) + 1
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    for chunk in chunks:
        blocks.append({
            "block_type": 23,
            "code": {
                "elements": _split_text_run(chunk),
                "style": {"language": 1},
            },
        })

    return blocks


# ============================================================
# 分类匹配
# ============================================================

def match_category(filename: str, categories: list[dict]) -> str:
    """按文件名匹配二级分类。支持两种格式：
    - 新格式: {name, match: ["pattern1", "pattern2"]}
    - 旧格式: {name, children: [...]} 或 {pattern: "...", node_title: "..."}
    """
    for cat in categories:
        name = cat.get("name", cat.get("node_title", ""))
        # 新格式：match 列表
        patterns = cat.get("match", [])
        for pat in patterns:
            if fnmatch.fnmatch(filename, pat):
                return name
        # 旧格式兼容：单个 pattern
        if "pattern" in cat and fnmatch.fnmatch(filename, cat["pattern"]):
            return name
    return "其他笔记"
