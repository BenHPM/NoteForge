"""
feishu_client.py — 飞书知识库 API 客户端（通过 lark-cli 调用）

功能：
  1. FeishuClient: 通过 lark-cli api 子进程调用飞书 API（用户身份，自动管理 token）
  2. md_to_blocks(): Markdown → 飞书 Block 转换
  3. match_category(): 按文件名匹配领域分类

用法：
  from feishu_client import FeishuClient, md_to_blocks, match_category
"""

import json
import logging
import os
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
        readonly: bool = False,
        api_interval: float = 0.5,
    ):
        self.space_id = space_id
        self.block_batch_size = block_batch_size
        self.dry_run = dry_run
        self.readonly = readonly
        self.api_interval = api_interval

        # 首次使用时查找 lark-cli
        if FeishuClient._lark_cli_path is None:
            FeishuClient._lark_cli_path = self._find_lark_cli()

        # P2.7: 确保 HERMES_HOME 目录存在（lark-cli 据此解析 hermes 工作区，
        # 目录缺失会让 doctor 误报 not_configured，虽不致命但增加排查噪音）
        hermes_home = os.environ.get("HERMES_HOME")
        if hermes_home and not os.path.isdir(hermes_home):
            try:
                os.makedirs(hermes_home, exist_ok=True)
                logger.debug(f"创建 HERMES_HOME 目录: {hermes_home}")
            except Exception as e:
                logger.debug(f"创建 HERMES_HOME 失败（非致命）: {e}")

    @staticmethod
    def _find_lark_cli() -> str:
        """查找 lark-cli 可执行文件。"""
        import sys
        # Windows: 优先用 .cmd 包装器（npm 生成的 PowerShell wrapper）
        if sys.platform == "win32":
            # 检查 npm 全局目录下的 lark-cli.cmd
            npm_global = os.path.expandvars(r"%APPDATA%\npm")
            cmd_path = os.path.join(npm_global, "lark-cli.cmd")
            if os.path.exists(cmd_path):
                logger.debug(f"找到 lark-cli.cmd: {cmd_path}")
                return cmd_path
            # 回退到 where 命令
            try:
                result = subprocess.run(
                    ["where", "lark-cli"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    # where 可能返回多个路径，优先选 .cmd
                    paths = result.stdout.strip().split("\n")
                    for p in paths:
                        p = p.strip()
                        if p.endswith(".cmd") and os.path.exists(p):
                            logger.debug(f"找到 lark-cli.cmd: {p}")
                            return p
                    # 否则用第一个
                    path = paths[0].strip()
                    if path and os.path.exists(path):
                        logger.debug(f"找到 lark-cli: {path}")
                        return path
            except Exception as e:
                logger.debug(f"lark-cli 查找失败: {e}")
        else:
            # Unix: 直接用 which
            try:
                result = subprocess.run(
                    ["which", "lark-cli"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    path = result.stdout.strip()
                    if path:
                        logger.debug(f"找到 lark-cli: {path}")
                        return path
            except Exception as e:
                logger.debug(f"lark-cli 查找失败: {e}")
        # 回退到默认名（依赖 PATH）
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
            # 写入临时文件到 output/logs/（lark-cli 要求相对路径，避免 CWD 污染）
            _tmp_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'output', 'logs')
            os.makedirs(_tmp_dir, exist_ok=True)
            tmp_file = tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False, dir=_tmp_dir, encoding='utf-8',
                prefix='_feishu_tmp_'
            )
            tmp_file.write(json.dumps(data, ensure_ascii=False))
            tmp_file.close()
            # lark-cli 从 CWD 运行，需要相对于 CWD 的路径
            tmp_rel = os.path.relpath(tmp_file.name, os.getcwd())
            cmd.extend(["--data", f"@{tmp_rel}"])
            logger.debug(f"发送数据: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}...")
        cmd.append("--json")

        if self.dry_run:
            # readonly（预览）模式：GET 真实放行（只读安全，让 dry-run 能读到真实 wiki 结构），
            # 写操作（POST/PATCH/DELETE）仍 mock，保证预览绝不产生副作用。
            # 纯 dry_run（readonly=False，单测契约）：全部 mock。
            if self.readonly and method == "GET":
                logger.info(f"[dry-run:readonly] GET {path} 真实读取")
            else:
                logger.info(f"[dry-run] API {method} {path}")
                if tmp_file:
                    os.unlink(tmp_file.name)
                return {"code": 0, "data": {}}

        logger.debug(f"API {method} {path} stdin={stdin_data is not None} tmpfile={tmp_file is not None}")
        try:
            # P2.8: 无 stdin 输入时显式关闭 stdin（DEVNULL），避免子进程偶发等待 stdin 挂起
            stdin_kwargs: dict[str, Any] = {}
            if stdin_data is not None:
                stdin_kwargs["input"] = stdin_data
            else:
                stdin_kwargs["stdin"] = subprocess.DEVNULL
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60,
                **stdin_kwargs,
            )
        finally:
            if tmp_file:
                try:
                    os.unlink(tmp_file.name)
                except Exception as e:
                    logger.warning(f"临时文件清理失败: {tmp_file.name}: {e}")

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
        # 注意：lark-cli 成功时返回 {"ok": true, "data": ...}，不含 code 字段
        # 只有错误时才有 code 字段（如 131006 权限不足）
        code = resp.get("code")
        if code is not None and code != 0:
            msg = resp.get("msg", "")
            logger.error(f"[API ERROR] code={code} msg={msg}")
            raise RuntimeError(f"飞书 API 错误 (code={code}): {msg}")

        return resp

    # ------ 知识库节点操作 ------

    def delete_wiki_node(self, space_id: str, node_token: str) -> bool:
        """删除 wiki 节点（通过 lark-cli api DELETE）。"""
        if self.dry_run:
            logger.info(f"[dry-run] 删除 wiki 节点: {node_token}")
            return True
        path = f"wiki/v2/spaces/{space_id}/nodes/{node_token}"
        try:
            self._api("DELETE", path)
            return True
        except Exception as e:
            logger.debug(f"删除节点 {node_token} 失败: {e}")
            return False

    def list_child_nodes(self, parent_node_token: str) -> list[dict]:
        """列出某节点的子节点（自动翻页，飞书每页最多 50 个）。

        必须翻页：超过 50 个子节点的分类（如地缘政治 107 篇）若不翻页，
        仅返回第一页，会导致 find_node_by_title 找不到第 2 页节点而重复创建。
        """
        logger.debug(f"list_child_nodes parent={parent_node_token}")
        items: list[dict] = []
        page_token = ""
        while True:
            params = {"parent_node_token": parent_node_token, "page_size": 50}
            if page_token:
                params["page_token"] = page_token
            data = self._api(
                "GET",
                f"wiki/v2/spaces/{self.space_id}/nodes",
                params=params,
            )
            resp_data = data.get("data", {})
            items.extend(resp_data.get("items", []))
            page_token = resp_data.get("page_token", "")
            if not page_token:
                break
        return items

    def find_node_by_title(self, parent_node_token: str, title: str) -> Optional[dict]:
        """在子节点中按标题查找；父节点不存在时返回 None 而非抛异常。"""
        if self.dry_run and not self.readonly:
            logger.info(f"[dry-run] 检查节点是否存在: {title}")
            return None
        try:
            children = self.list_child_nodes(parent_node_token)
        except RuntimeError as e:
            logger.warning(f"  查找子节点失败（父节点 {parent_node_token}）: {e}")
            return None
        for child in children:
            if child.get("title") == title:
                return child
        return None

    def create_node(self, parent_node_token: str, title: str, obj_type: str = "docx") -> dict:
        """创建知识库节点。返回节点信息（含 node_token 和 obj_token）。"""
        if self.dry_run and not self.readonly:
            logger.info(f"[dry-run] 将创建 {obj_type} 节点: {title} (parent={parent_node_token})")
            return {"node_token": f"dry-run-{hash(title) % 100000}", "obj_type": obj_type, "title": title}

        # readonly 预览模式：不真正创建，但按真实结构判断——若节点不存在则记录"将创建"
        if self.readonly and self.dry_run:
            existing = self.find_node_by_title(parent_node_token, title)
            if existing:
                return existing
            logger.info(f"[dry-run:readonly] 将创建 {obj_type} 节点: {title} (parent={parent_node_token})")
            # 返回伪 token 但标记将被创建（child index 构建会容忍其不存在）
            return {"node_token": f"dry-run-new-{hash(title) % 100000}", "obj_type": obj_type, "title": title}

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
        time.sleep(self.api_interval)
        self.append_blocks(document_id, blocks)

    def append_blocks(self, document_id: str, blocks: list[dict]) -> None:
        """分批追加 blocks 到文档末尾。"""
        if self.dry_run:
            logger.info(f"[dry-run] 将向文档 {document_id} 写入 {len(blocks)} 个 block")
            return

        total = len(blocks)
        for i in range(0, total, self.block_batch_size):
            batch = blocks[i:i + self.block_batch_size]
            self._append_batch_with_retry(document_id, batch, i, total)
            logger.info(f"  写入 block {i + 1}-{i + len(batch)}/{total}")

        if total > 0:
            time.sleep(self.api_interval)

    def _append_batch_with_retry(
        self, document_id: str, batch: list[dict], start_idx: int, total: int,
    ) -> None:
        """写入一批 block，遇到飞书内部同步延迟时自动重试。"""
        max_retries = 3
        base_delay = 2.0
        for attempt in range(max_retries):
            try:
                self._api(
                    "POST",
                    f"docx/v1/documents/{document_id}/blocks/{document_id}/children",
                    data={"children": batch, "index": -1},
                )
                return
            except RuntimeError as e:
                err_msg = str(e).lower()
                # 飞书文档创建后需要一定时间才能在 block API 中访问
                is_transient = (
                    "resource deleted" in err_msg
                    or "not found" in err_msg
                    or "node not found" in err_msg
                )
                if is_transient and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"  写入 block {start_idx + 1}-{start_idx + len(batch)}/{total} "
                        f"失败（{e}），{delay}s 后重试（{attempt + 1}/{max_retries - 1}）",
                    )
                    time.sleep(delay)
                    continue
                raise

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
            time.sleep(2.0)

        try:
            self.append_blocks(obj_token, blocks)
        except RuntimeError:
            # 如果首次写入失败，可能是 obj_token 尚未就绪，尝试用 node_token 兜底
            node_token = node.get("node_token", "")
            if node_token and node_token != obj_token:
                logger.warning(f"  使用 node_token 重试写入 {title}...")
                time.sleep(3.0)
                self.append_blocks(node_token, blocks)
            else:
                raise
        return obj_token

    def ensure_category_node(self, root_node_token: str, category_title: str) -> str:
        """确保分类节点存在，返回 node_token。
        - 先查找无前缀的旧节点（如 "跨集提炼"），找到则复用
        - 找不到则创建带 📁 前缀的新节点（如 "📁 跨集提炼"）
        """
        # 兼容旧节点：先找无前缀版本
        existing = self.find_node_by_title(root_node_token, category_title)
        if existing:
            return existing.get("node_token", "")
        # 新节点：加 📁 前缀
        prefixed = f"📁 {category_title}"
        node = self.create_node(root_node_token, prefixed, obj_type="docx")
        return node.get("node_token", "")


from noteforge.integration.feishu_blocks import md_to_blocks
from noteforge.integration.feishu_category import match_category

