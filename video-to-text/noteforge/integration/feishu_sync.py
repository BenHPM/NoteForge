#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feishu_sync.py — NoteForge 本地笔记 → 飞书知识库同步脚本（薄包装）

功能：
  1. 读取配置文件（llm_engine_config.yaml 的 feishu 段）
  2. 扫描本地笔记文件，按 categories 规则分组
  3. 调用 FeishuClient 执行同步

用法：
  python -m noteforge.integration.feishu_sync                # 同步所有笔记
  python -m noteforge.integration.feishu_sync --dry-run      # 预览模式
  python -m noteforge.integration.feishu_sync --file "第01集.md"  # 同步单个文件
  python -m noteforge.integration.feishu_sync --category "课程笔记"  # 同步某个分类
  python -m noteforge.integration.feishu_sync --new-only     # 只同步新增（跳过已存在的）

依赖：requests、pyyaml
"""

import argparse
import fnmatch
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, NamedTuple

logger = logging.getLogger('noteforge.feishu_sync')

# 飞书 Wiki 链接域名（根据部署区域调整）
FEISHU_WIKI_DOMAIN = "feishu.cn"

# 项目根目录：noteforge/integration/ → video-to-text/ → NoteForge/
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # noteforge/integration/ -> video-to-text/
PROJECT_ROOT = BASE_DIR.parent  # video-to-text/ -> NoteForge/
CONFIG_PATH = BASE_DIR / "config" / "llm_engine_config.yaml"
HASH_CACHE_FILE = BASE_DIR / "output" / "logs" / ".sync_hash_cache.json"

from noteforge.integration.feishu import FeishuClient, md_to_blocks, match_category


# ============================================================
# B3: 独立同步验证 — 不信任上游标记，飞书同步前独立校验笔记质量
# ============================================================

# 拒绝文本模式（与 note_formatter.py REFUSAL_PATTERNS 对齐，但独立维护）
_SYNC_REFUSAL_PATTERNS = [
    r'I\s+cannot\s+(?:complete|fulfill|generate|provide|assist)',
    r"I'm\s+(?:unable|not able)\s+to\s+(?:complete|fulfill|generate|provide)",
    r'as\s+an\s+AI\s+(?:language\s+model|assistant)',
    r'this\s+request\s+was\s+rejected',
    r'considered\s+high\s+risk',
    r'content\s+policy\s+violation',
    r'我\s*(?:无法|不能|不可以)\s*(?:完成|生成|提供|协助)',
    r'作为\s*(?:AI|人工智能|语言模型)',
    r'内容\s*(?:违反|违规|敏感)',
]

_SYNC_REFUSAL_RE = re.compile('|'.join(_SYNC_REFUSAL_PATTERNS), re.IGNORECASE)

# LLM_REFUSAL_DETECTED 标记（由 note_formatter.py 添加）
_REFUSAL_MARKER = 'LLM_REFUSAL_DETECTED'


def can_sync(content: str, filename: str = "") -> tuple[bool, list[str]]:
    """B3: 独立验证笔记是否可安全同步到飞书

    不信任上游标记（如 LLM_REFUSAL_DETECTED），独立执行结构校验。
    这是飞书同步前的最后一道防线。

    Args:
        content: 笔记全文（Markdown）
        filename: 文件名（用于日志和诊断）

    Returns:
        (can_sync, reasons): 是否可同步 + 阻止原因列表
    """
    reasons = []

    # 1. 长度检查：过短可能是生成失败/过滤
    body_text = content.strip()
    # 去掉标题/元数据/分隔线后的实质内容
    for line in body_text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('---') or stripped.startswith('*'):
            body_text = body_text.replace(line, '', 1)
    body_text = body_text.strip()

    if len(body_text) < 100:
        reasons.append(f"实质内容过短 ({len(body_text)} 字 < 100 字下限)")

    # 2. Section 数量检查：至少应有标题 + 1 个内容节
    sections = re.findall(r'^##\s+', content, re.MULTILINE)
    if len(sections) < 1:
        reasons.append(f"缺少二级标题节（仅 {len(sections)} 个）")

    # 3. 拒绝文本检测（独立于 note_formatter 的标记）
    refusal_match = _SYNC_REFUSAL_RE.search(content)
    if refusal_match:
        reasons.append(f"检测到 LLM 拒绝文本: '{refusal_match.group()[:50]}'")

    # 4. 上游标记检查（LLM_REFUSAL_DETECTED）
    if _REFUSAL_MARKER in content:
        reasons.append(f"上游标记 LLM_REFUSAL_DETECTED 存在")

    # 5. 实体密度检查：如果笔记几乎全是标题/列表标记，可能无实质内容
    content_lines = [
        l for l in content.split('\n')
        if l.strip()
        and not l.strip().startswith('#')
        and not l.strip().startswith('---')
        and not l.strip().startswith('*')
        and len(l.strip()) > 5
    ]
    if len(content_lines) < 3:
        reasons.append(f"实质内容行过少 ({len(content_lines)} 行 < 3 行下限)")

    can = len(reasons) == 0
    if not can:
        logger.warning(
            "笔记未通过同步验证 (%s): %s",
            filename or "<unknown>",
            '; '.join(reasons),
        )
    return can, reasons


class SyncItem(NamedTuple):
    """单篇笔记的同步结果"""
    title: str
    action: str        # "created" / "updated" / "skipped"
    node_token: str
    category: str       # 所属分类路径，如 "金融投资/逐集笔记"
    cat_node_token: str  # 分类父节点 token（用于生成分类链接）


def _load_hash_cache() -> dict:
    if HASH_CACHE_FILE.exists():
        return json.loads(HASH_CACHE_FILE.read_text(encoding='utf-8'))
    return {}


def _save_hash_cache(cache: dict):
    HASH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    HASH_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), 'utf-8')


def _content_hash(content: str) -> str:
    return hashlib.md5(content.encode('utf-8')).hexdigest()[:12]


def _clean_title(stem: str) -> str:
    """清理标题：去掉 emoji 前缀、版本后缀、前缀序号，返回稳定的纯标题"""
    title = stem.replace("📁 ", "").replace("📄 ", "").strip()
    if title.endswith('_v5'):
        title = title[:-3]
    # 去掉已有前缀序号（如 "1. "、"18. "、"第01集-"）
    title = re.sub(r'^\d+\.\s*', '', title)
    title = re.sub(r'^第\s*\d+\s*集\s*-\s*', '', title)
    return title


def _renumber_category(client: FeishuClient, parent_token: str, files: list[Path]) -> None:
    """对飞书分类下的文档按飞书当前顺序重新编号。

    解决多次同步/新增笔记导致的序号重复/乱序问题。
    规则：
    - 按飞书 API 返回的节点顺序编号 1..N（保持用户在飞书上看到的顺序）
    - 标题格式："{idx}. {clean_title}"
    - 通过 clean_title 匹配本地文件，如果匹配则使用本地文件的稳定标题
    - 检测重复节点（相同 clean_title 多个），保留第一个，删除多余
    """
    if not files:
        return

    # 获取飞书当前子节点（保持飞书 API 返回的顺序）
    children = client.list_child_nodes(parent_token)

    # 建立 clean_title -> 节点列表（可能有重复）
    from collections import defaultdict
    ordered_nodes: list[tuple[str, str]] = []  # [(token, title), ...] 保持飞书顺序
    clean_to_count: dict[str, int] = defaultdict(int)
    for child in children:
        token = child.get("node_token", "")
        title = child.get("title", "")
        if token and title:
            ordered_nodes.append((token, title))
            clean = _clean_title(title)
            clean_to_count[clean] += 1

    # 1. 清理重复节点（相同 clean_title 保留第一个，删除多余）
    duplicates_to_delete: list[tuple[str, str]] = []
    seen_cleans: set[str] = set()
    for token, title in ordered_nodes:
        clean = _clean_title(title)
        if clean in seen_cleans:
            duplicates_to_delete.append((token, title))
        else:
            seen_cleans.add(clean)

    if duplicates_to_delete:
        logger.info("  发现 %d 个重复节点，准备删除", len(duplicates_to_delete))
        for token, title in duplicates_to_delete:
            logger.info(f"    删除重复: {title}")
            try:
                _delete_wiki_node(client.space_id, token)
            except Exception as e:
                logger.warning(f"    删除失败: {e}")
        # 从列表中移除已删除的节点
        ordered_nodes = [(t, tl) for t, tl in ordered_nodes if (t, tl) not in duplicates_to_delete]

    # 2. 按飞书当前顺序重编号
    to_rename: list[tuple[str, str, str]] = []
    for idx, (token, old_title) in enumerate(ordered_nodes, 1):
        clean = _clean_title(old_title)
        new_title = f"{idx}. {clean}"
        if old_title != new_title:
            to_rename.append((token, old_title, new_title))

    if not to_rename:
        return

    logger.info("  自动重编号：%d 个节点需要重命名", len(to_rename))
    for token, old_title, new_title in to_rename:
        logger.info(f"    {old_title} → {new_title}")
        try:
            _rename_wiki_node(client.space_id, token, new_title)
        except Exception as e:
            logger.warning(f"    重命名失败: {e}")


def _rename_wiki_node(space_id: str, node_token: str, new_title: str) -> bool:
    """通过飞书 API 重命名 wiki 节点

    由于 lark-cli 的 PATCH 请求对 wiki 节点更新不稳定，
    改用删除+重建的方式实现重命名。
    """
    # 先尝试 PATCH（如果 lark-cli 版本支持）
    try:
        cmd = [
            _find_lark_cli(), "api", "--as", "user",
            "PATCH", f"wiki/v2/spaces/{space_id}/nodes/{node_token}",
        ]
        import tempfile, json, os
        _tmp_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'output', 'logs')
        os.makedirs(_tmp_dir, exist_ok=True)
        tmp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False, dir=_tmp_dir, encoding='utf-8',
            prefix='_feishu_rename_'
        )
        tmp_file.write(json.dumps({"title": new_title}, ensure_ascii=False))
        tmp_file.close()
        tmp_rel = os.path.relpath(tmp_file.name, os.getcwd())
        cmd.extend(["--data", f"@{tmp_rel}"])
        cmd.append("--json")

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        os.unlink(tmp_file.name)
        if result.returncode == 0:
            try:
                resp = json.loads(result.stdout)
                if resp.get("ok"):
                    return True
            except json.JSONDecodeError:
                pass
    except Exception:
        pass

    # PATCH 失败，返回 False（由调用方决定后续处理）
    logger.warning(f"  PATCH 重命名失败，节点 {node_token} 需要手动处理")
    return False


def _load_env_file() -> None:
    """从项目根目录 .env 文件加载环境变量（不覆盖已有的）。"""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def _load_config() -> dict:
    """加载配置文件。"""
    from noteforge.config import load_yaml
    if not CONFIG_PATH.exists():
        logger.error("配置文件不存在: %s", CONFIG_PATH)
        sys.exit(1)
    return load_yaml(str(CONFIG_PATH))


def _get_feishu_config(config: dict) -> dict:
    """从配置中提取 feishu 段，环境变量优先于 YAML 配置。

    优先级：os.environ > YAML 配置值。这确保 .env 中的值始终覆盖 YAML，
    同时保持向后兼容（YAML 值作为回退）。
    """
    feishu = config.get("feishu", {})
    if not feishu.get("enabled", False):
        logger.warning("飞书同步未启用（feishu.enabled = false）")
        logger.info("请在 config/llm_engine_config.yaml 中设置 feishu.enabled: true")
        sys.exit(0)
    # 环境变量优先：os.environ 中的值覆盖 YAML 配置
    env_space_id = os.environ.get("FEISHU_SPACE_ID", "")
    env_root_node_token = os.environ.get("FEISHU_ROOT_NODE_TOKEN", "")
    if env_space_id:
        feishu["space_id"] = env_space_id
    elif not feishu.get("space_id"):
        # YAML 也为空时，留空（后续 required 检查会报错）
        feishu["space_id"] = ""
    if env_root_node_token:
        feishu["root_node_token"] = env_root_node_token
    elif not feishu.get("root_node_token"):
        feishu["root_node_token"] = ""
    required = ["space_id", "root_node_token"]
    for key in required:
        if not feishu.get(key):
            logger.error("缺少配置项: feishu.%s（可设置环境变量 FEISHU_%s）", key, key.upper())
            sys.exit(1)
    return feishu


def scan_notes() -> tuple[dict[str, list[tuple[str, Path]]], set[str]]:
    """
    扫描笔记文件并按叶子分类分组（支持多级嵌套）。

    Returns:
        (groups, matched_files):
        - groups: {叶子分类路径: [(文件名, 文件路径), ...]}
          路径用 "/" 分隔，如 "AI笔记库/短视频导演课程/📖 逐集笔记"
        - matched_files: 已匹配的文件名集合
    """
    config = _load_config()
    feishu = config.get("feishu", {})
    categories = feishu.get("categories", [])
    exclude_patterns = feishu.get("exclude_patterns", [])
    notes_dir = BASE_DIR / "output" / "notes"

    groups: dict[str, list[tuple[str, Path]]] = {}
    matched_files: set[str] = set()

    if not notes_dir.exists():
        logger.warning("笔记目录不存在: %s", notes_dir)
        return groups, matched_files

    # 收集所有文件
    all_files: list[tuple[str, Path]] = []
    for md_file in sorted(notes_dir.glob("*.md")):
        filename = md_file.name
        if any(fnmatch.fnmatch(filename, p) for p in exclude_patterns):
            logger.info("排除文件: %s", filename)
            continue
        all_files.append((filename, md_file))

    def _stable_key(filename: str) -> str:
        """生成稳定的笔记标识（去掉版本后缀 + 前缀序号）用于去重匹配"""
        stem = Path(filename).stem
        stem = stem.replace("📁 ", "").replace("📄 ", "").strip()
        # 去掉 _v5 版本后缀
        if stem.endswith('_v5'):
            stem = stem[:-3]
        # 去掉前缀序号（如 "1. "、"18. "、"第01集-"）
        stem = re.sub(r'^\d+\.\s*', '', stem)
        stem = re.sub(r'^第\s*\d+\s*集\s*-\s*', '', stem)
        return stem

    # 扫描 spnr 目录（如果存在）
    spnr_file = PROJECT_ROOT / "spnr" / "nr" / "视频笔记.md"
    if spnr_file.exists():
        filename = spnr_file.name
        if not any(fnmatch.fnmatch(filename, p) for p in exclude_patterns):
            all_files.append((filename, spnr_file))

    def _match_leaf(node: dict, path: str) -> None:
        """匹配二级分类 + 内部固定子结构。
        - 普通分类：跨集提炼 / 逐集笔记（跨集在上）
        - 其他笔记：无子结构，直接平铺（暂存池）
        支持新格式 (match 列表) 和旧格式 (pattern/children)。
        """
        patterns = node.get("match", [])
        if patterns:
            is_other = node.get("name", "") == "其他笔记"
            for filename, filepath in all_files:
                if filename not in matched_files:
                    for pat in patterns:
                        if fnmatch.fnmatch(filename, pat):
                            if is_other:
                                # 其他笔记：无子结构，直接平铺
                                target_path = path
                            elif any(fnmatch.fnmatch(filename, sp) for sp in
                                     ["*知识体系*", "*跨集*", "*提炼*", "*框架*", "*模型*"]):
                                target_path = f"{path}/跨集提炼"
                            else:
                                target_path = f"{path}/逐集笔记"
                            if target_path not in groups:
                                groups[target_path] = []
                            groups[target_path].append((filename, filepath))
                            matched_files.add(filename)
                            break
            return
        # 旧格式兼容：递归 children
        children = node.get("children", [])
        if children:
            for child in children:
                child_path = f"{path}/{child.get('name', '')}"
                _match_leaf(child, child_path)
        elif "pattern" in node:
            pattern = node["pattern"]
            for filename, filepath in all_files:
                if filename not in matched_files and fnmatch.fnmatch(filename, pattern):
                    if path not in groups:
                        groups[path] = []
                    groups[path].append((filename, filepath))
                    matched_files.add(filename)

    for cat_config in categories:
        cat_path = cat_config.get("name", "")
        _match_leaf(cat_config, cat_path)

    total = sum(len(files) for files in groups.values())
    logger.info("扫描到 %d 个笔记文件，分为 %d 个分类", total, len(groups))
    for cat, files in groups.items():
        logger.info("  - %s: %d 篇", cat, len(files))

    return groups, matched_files


def _sync_node(
    client: FeishuClient,
    node_config: dict,
    parent_node_token: str,
    groups: dict[str, list[tuple[str, Path]]],
    path: str,
    file_filter: Optional[str],
    new_only: bool,
    dry_run: bool,
    sync_items: list[SyncItem],
    hash_cache: dict,
) -> tuple[int, int, int]:
    """
    递归同步一个分类节点。

    Returns:
        (synced, skipped, errors)
    """
    node_name = node_config.get("name") or node_config.get("node_title", "")
    children = node_config.get("children", [])
    pattern = node_config.get("pattern")
    match_patterns = node_config.get("match", [])
    synced = skipped = errors = 0

    if children:
        # 中间节点（旧格式）：确保节点存在，递归处理子节点
        logger.info("  %s%s/", '  ' * path.count('/'), node_name)
        node_token = client.ensure_category_node(parent_node_token, node_name)

        for child in children:
            child_path = f"{path}/{child.get('name', '')}"
            s, sk, e = _sync_node(
                client, child, node_token, groups, child_path,
                file_filter, new_only, dry_run, sync_items, hash_cache,
            )
            synced += s
            skipped += sk
            errors += e

    elif match_patterns:
        # 二级分类（新格式）
        is_other = node_name == "其他笔记"
        logger.info("  %s/", node_name)
        cat_token = client.ensure_category_node(parent_node_token, node_name)

        if is_other:
            # 其他笔记：无子结构，直接平铺
            files = groups.get(path, [])
            sub_nodes_to_sync = [(cat_token, files, path)] if files else []
        else:
            # 普通分类：跨集提炼在上，逐集笔记在下
            sub_nodes_to_sync = []
            for sub_name in ["跨集提炼", "逐集笔记"]:
                sub_path = f"{path}/{sub_name}"
                files = groups.get(sub_path, [])
                if files:
                    indent = "  " + "  "
                    logger.info("  %s%s (%d 篇)", indent, sub_name, len(files))
                    sub_token = client.ensure_category_node(cat_token, sub_name)
                    sub_nodes_to_sync.append((sub_token, files, sub_path))

        for sub_token, files, sub_path in sub_nodes_to_sync:
            indent = "  " + "  " if not is_other else "  "
            is_synth = "跨集" in sub_path
            for idx, (filename, filepath) in enumerate(files, 1):
                if file_filter and file_filter not in filename:
                    continue

                # 稳定标题：纯文件名 stem（去 _v5），不带序号
                title = _clean_title(filepath.stem)
                logger.info("  %s[%d/%d] %s", indent, idx, len(files), title)

                try:
                    content = filepath.read_text(encoding="utf-8")
                except Exception as e:
                    logger.error("  %s  读取失败: %s", indent, e)
                    errors += 1
                    continue

                # B3: 独立同步验证 — 不信任上游标记
                can, sync_reasons = can_sync(content, filename)
                if not can:
                    logger.warning(
                        "  %s  ⛔ 同步验证未通过: %s",
                        indent, '; '.join(sync_reasons),
                    )
                    errors += 1
                    continue

                blocks = md_to_blocks(content)
                logger.info("  %s  解析得到 %d 个 block", indent, len(blocks))

                existing = client.find_node_by_title(sub_token, title)
                # 兼容旧数据：尝试去掉前缀序号后查找
                if not existing:
                    base = re.sub(r'^\d+\.\s*', '', title)
                    if base != title:
                        existing = client.find_node_by_title(sub_token, base)
                if existing:
                    if new_only:
                        logger.info("  %s  已存在，跳过（--new-only）", indent)
                        skipped += 1
                        sync_items.append(SyncItem(title, "skipped",
                            existing.get("node_token", ""), path, sub_token))
                        continue
                    # 内容 hash 比对，未变化则跳过
                    content_hash = _content_hash(content)
                    cache_key = f"{path}/{title}"
                    if hash_cache.get(cache_key) == content_hash:
                        logger.info("  %s  内容未变化，跳过", indent)
                        skipped += 1
                        sync_items.append(SyncItem(title, "skipped",
                            existing.get("node_token", ""), path, sub_token))
                        continue
                    # 更新已有文档
                    try:
                        doc_token = existing.get("obj_token", "")
                        client.overwrite_document(doc_token, blocks)
                        logger.info("  %s  已更新", indent)
                        synced += 1
                        hash_cache[cache_key] = content_hash
                        sync_items.append(SyncItem(title, "updated",
                            existing.get("node_token", ""), path, sub_token))
                    except Exception as e:
                        logger.error("  %s  更新失败: %s", indent, e)
                        errors += 1
                else:
                    # 创建新文档
                    try:
                        obj_token = client.create_document_and_write(sub_token, title, blocks)
                        logger.info("  %s  已创建", indent)
                        synced += 1
                        hash_cache[f"{path}/{title}"] = _content_hash(content)
                        sync_items.append(SyncItem(title, "created",
                            obj_token or "", path, sub_token))
                    except Exception as e:
                        logger.error("  %s  创建失败: %s", indent, e)
                        errors += 1

            # 同步完成后自动重编号（仅逐集笔记，非跨集提炼）
            if not is_synth and not is_other and not dry_run:
                _renumber_category(client, sub_token, [f for _, f in files if not file_filter or file_filter in f.name])

    elif pattern:
        # 叶子节点：同步匹配的文件
        files = groups.get(path, [])
        if not files:
            return 0, 0, 0

        indent = "  " * (path.count('/') + 1)
        logger.info("  %s📄 %s (%d 篇)", indent, node_name, len(files))

        # 确保父节点存在（叶子节点需要一个容器）
        node_token = client.ensure_category_node(parent_node_token, node_name)

        for idx, (filename, filepath) in enumerate(files, 1):
            if file_filter and file_filter not in filename:
                continue

            title = _clean_title(filepath.stem)
            logger.info("  %s[%d/%d] %s", indent, idx, len(files), title)

            try:
                content = filepath.read_text(encoding="utf-8")
            except Exception as e:
                logger.error("  %s  读取失败: %s", indent, e)
                errors += 1
                continue

            blocks = md_to_blocks(content)
            logger.info("  %s  解析得到 %d 个 block", indent, len(blocks))

            existing = client.find_node_by_title(node_token, title)
            if existing:
                if new_only:
                    logger.info("  %s  已存在，跳过（--new-only）", indent)
                    skipped += 1
                    sync_items.append(SyncItem(title, "skipped",
                        existing.get("node_token", ""), path, node_token))
                    continue
                # 内容 hash 比对
                content_hash = _content_hash(content)
                cache_key = f"{path}/{title}"
                if hash_cache.get(cache_key) == content_hash:
                    logger.info("  %s  内容未变化，跳过", indent)
                    skipped += 1
                    continue
                obj_token = existing.get("obj_token") or existing.get("node_token", "")
                logger.info("  %s  已存在，更新内容...", indent)
                try:
                    client.overwrite_document(obj_token, blocks)
                    logger.info("  %s  已更新", indent)
                    synced += 1
                    hash_cache[cache_key] = content_hash
                    sync_items.append(SyncItem(title, "updated",
                        existing.get("node_token", ""), path, node_token))
                except Exception as e:
                    logger.error("  %s  更新失败: %s", indent, e)
                    errors += 1
            else:
                try:
                    obj_token = client.create_document_and_write(node_token, title, blocks)
                    logger.info("  %s  已创建", indent)
                    synced += 1
                    sync_items.append(SyncItem(title, "created",
                        obj_token or "", path, node_token))
                except Exception as e:
                    logger.error("  %s  创建失败: %s", indent, e)
                    errors += 1

    return synced, skipped, errors


def _collect_all_titles(client: FeishuClient, node_token: str) -> set[str]:
    """递归收集节点下所有文档标题（叶子节点）"""
    titles = set()
    children = client.list_child_nodes(node_token)
    for child in children:
        title = child.get("title", "")
        child_token = child.get("node_token", "")
        # 飞书 API 中 obj_type 对文件夹和文档都是 "docx"
        # 通过是否有子节点来区分：有子节点的是文件夹，递归；否则是文档
        if child_token:
            sub_children = client.list_child_nodes(child_token)
            if sub_children:
                # 文件夹节点，递归
                titles.update(_collect_all_titles(client, child_token))
            elif title:
                # 叶子文档节点
                titles.add(title)
        elif title:
            titles.add(title)
    return titles


def _delete_wiki_node(space_id: str, node_token: str) -> bool:
    """通过 lark-cli 专用命令删除 wiki 节点（支持异步轮询）"""
    try:
        cmd = [
            _find_lark_cli(), "wiki", "+node-delete",
            "--space-id", space_id,
            "--node-token", node_token,
            "--obj-type", "wiki",
            "--yes", "--as", "user", "--json",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        if result.returncode == 0:
            resp = json.loads(result.stdout) if result.stdout.strip() else {}
            if resp.get("ok") or resp.get("data", {}).get("status") == "success":
                return True
        return False
    except Exception as e:
        logger.debug(f"节点检查失败: {e}")
        return False


def _find_lark_cli() -> str:
    """查找 lark-cli 路径（委托给 FeishuClient，避免重复实现）"""
    # 触发 FeishuClient 的惰性初始化
    from noteforge.integration.feishu import FeishuClient
    if FeishuClient._lark_cli_path is None:
        FeishuClient._lark_cli_path = FeishuClient._find_lark_cli()
    return FeishuClient._lark_cli_path


def _cleanup_other_notes(
    client: FeishuClient,
    feishu: dict,
    root_node: str,
    categories: list[dict],
    dry_run: bool,
) -> int:
    """清理「其他笔记」中已被其他分类收录的旧文档。

    当笔记从「其他笔记」迁移到具体分类（如「金融投资」）后，
    飞书上的旧条目不会自动删除。此函数在同步后自动清理。
    """
    # 找到「其他笔记」和其他分类的节点
    root_children = client.list_child_nodes(root_node)
    other_node = None
    category_nodes = []
    for child in root_children:
        title = child.get("title", "")
        token = child.get("node_token", "")
        clean_title = title.replace("📁 ", "").strip()
        if clean_title == "其他笔记":
            other_node = (token, child)
        elif token:
            category_nodes.append((token, title))

    if not other_node:
        return 0

    other_token = other_node[0]

    # 收集其他分类中所有文档标题
    classified_titles: set[str] = set()
    for token, name in category_nodes:
        classified_titles.update(_collect_all_titles(client, token))

    if not classified_titles:
        return 0

    # 列出「其他笔记」中的文档
    other_children = client.list_child_nodes(other_token)
    to_delete = []
    for child in other_children:
        title = child.get("title", "")
        if title and title in classified_titles:
            to_delete.append((child.get("node_token", ""), title))

    if not to_delete:
        return 0

    logger.info("清理「其他笔记」中已被其他分类收录的文档:")
    cleaned = 0
    for node_token, title in to_delete:
        if dry_run:
            logger.info("  [DRY-RUN] 将删除: %s", title)
            cleaned += 1
        else:
            try:
                if _delete_wiki_node(feishu['space_id'], node_token):
                    logger.info("  已删除: %s", title)
                    cleaned += 1
                else:
                    logger.error("  删除失败: %s", title)
            except Exception as e:
                logger.error("  删除失败: %s (%s)", title, e)

    logger.info("清理: %d 篇", cleaned)
    return cleaned


def _print_sync_summary(
    sync_items: list[SyncItem],
    feishu: dict,
    cleaned: int,
) -> None:
    """打印同步结果汇总，区分新笔记/更新笔记/合成文档更新。

    - 新增笔记（created + 非合成）：🆕
    - 更新笔记（updated + 非合成）：📝
    - 合成文档（created/updated + 标题含知识体系）：🔬
    - 清理的重复文档：🧹
    """
    if not sync_items and cleaned == 0:
        return

    # 兼容 Windows GBK 控制台：emoji 无法编码时自动替换为 ?
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

    space_id = feishu.get("space_id", "")
    base_url = f"https://{FEISHU_WIKI_DOMAIN}/wiki"

    # 分类统计
    active = [i for i in sync_items if i.action in ("created", "updated")]
    skipped = [i for i in sync_items if i.action == "skipped"]

    if not active and cleaned == 0:
        return

    def is_synthesis(title: str) -> bool:
        return any(k in title for k in ['知识体系', '跨集', '提炼', '框架模型', '综合合成'])

    # 分离合成文档和单集笔记
    new_notes = [i for i in active if i.action == "created" and not is_synthesis(i.title)]
    updated_notes = [i for i in active if i.action == "updated" and not is_synthesis(i.title)]
    synthesis = [i for i in active if is_synthesis(i.title)]

    print(f"\n  📋 同步结果详情:")

    # 1. 新增笔记
    if new_notes:
        by_cat: dict[str, list[SyncItem]] = {}
        for item in new_notes:
            cat_label = item.category.split("/")[0] if "/" in item.category else item.category
            by_cat.setdefault(cat_label, []).append(item)
        print(f"    🆕 新增笔记: {len(new_notes)} 篇")
        for cat, items in by_cat.items():
            if len(items) <= 3:
                for item in items:
                    url = f"{base_url}/{item.node_token}" if item.node_token else ""
                    print(f"       {cat}/{item.title} → {url}")
            else:
                print(f"       {cat}: {len(items)} 篇")

    # 2. 更新笔记
    if updated_notes:
        print(f"    📝 更新笔记: {len(updated_notes)} 篇")
        by_cat2: dict[str, list[SyncItem]] = {}
        for item in updated_notes:
            cat_label = item.category.split("/")[0] if "/" in item.category else item.category
            by_cat2.setdefault(cat_label, []).append(item)
        for cat, items in by_cat2.items():
            print(f"       {cat}: {len(items)} 篇")

    # 3. 合成文档更新
    if synthesis:
        print(f"    🔬 合成文档更新: {len(synthesis)} 篇")
        for item in synthesis:
            url = f"{base_url}/{item.node_token}" if item.node_token else ""
            cat_label = item.category.split("/")[0] if "/" in item.category else item.category
            print(f"       {cat_label}/{item.title} → {url}")

    # 4. 跳过
    if skipped:
        print(f"    ⏭️  内容未变化跳过: {len(skipped)} 篇")

    # 5. 清理
    if cleaned > 0:
        print(f"    🧹 清理旧文档: {cleaned} 篇")


def run_sync(
    dry_run: bool = False,
    file_filter: Optional[str] = None,
    category_filter: Optional[str] = None,
    new_only: bool = False,
) -> None:
    """执行同步流程（支持多级嵌套结构）。"""
    _load_env_file()  # 加载 .env 环境变量
    config = _load_config()
    feishu = _get_feishu_config(config)

    print("=" * 60)
    print("  NoteForge → 飞书知识库同步")
    print(f"  项目根目录: {PROJECT_ROOT}")
    print(f"  模式: {'DRY-RUN' if dry_run else '正式同步'}")
    if file_filter:
        print(f"  过滤: 文件 '{file_filter}'")
    if category_filter:
        print(f"  过滤: 分类 '{category_filter}'")
    if new_only:
        print(f"  策略: 仅同步新增")
    print("=" * 60)

    client = FeishuClient(
        space_id=feishu["space_id"],
        block_batch_size=feishu.get("block_batch_size", 50),
        dry_run=dry_run,
        api_interval=feishu.get("api_interval", 0.5),
    )

    root_node = feishu["root_node_token"]
    categories = feishu.get("categories", [])

    # 扫描并分组
    groups, _ = scan_notes()

    # 过滤分类（按路径前缀匹配）
    if category_filter:
        filtered_groups = {
            k: v for k, v in groups.items()
            if category_filter in k
        }
        if not filtered_groups:
            logger.error("未找到分类: %s", category_filter)
            logger.info("可用分类: %s", ', '.join(groups.keys()))
            sys.exit(1)
        groups = filtered_groups

    # 递归同步
    synced = skipped = errors = 0
    sync_items: list[SyncItem] = []
    hash_cache = _load_hash_cache()

    for cat_config in categories:
        cat_path = cat_config.get("name", "")
        s, sk, e = _sync_node(
            client, cat_config, root_node, groups, cat_path,
            file_filter, new_only, dry_run, sync_items, hash_cache,
        )
        synced += s
        skipped += sk
        errors += e

    # 清理「其他笔记」中已被其他分类收录的旧文档
    cleaned = 0
    if not category_filter:
        cleaned = _cleanup_other_notes(
            client, feishu, root_node, categories, dry_run
        )

    # 汇总
    print("\n" + "=" * 60)
    print(f"  同步完成！")
    print(f"  知识库 space_id: {feishu['space_id']}")
    print(f"  同步: {synced} | 跳过: {skipped} | 错误: {errors}")
    print("=" * 60)

    # 保存内容 hash 缓存（下次同步时跳过未变化的文档）
    _save_hash_cache(hash_cache)

    # 详细结果 + 链接
    _print_sync_summary(sync_items, feishu, cleaned)


def clean_and_resync(dry_run: bool = False) -> None:
    """删除飞书上的所有内容后重新同步。"""
    _load_env_file()  # 加载 .env 环境变量
    config = _load_config()
    feishu = _get_feishu_config(config)

    print("=" * 60)
    print("  NoteForge → 飞书知识库 清理并重新同步")
    print(f"  项目根目录: {PROJECT_ROOT}")
    print(f"  模式: {'DRY-RUN' if dry_run else '正式执行'}")
    print("=" * 60)

    client = FeishuClient(
        space_id=feishu["space_id"],
        block_batch_size=feishu.get("block_batch_size", 50),
        dry_run=dry_run,
        api_interval=feishu.get("api_interval", 0.5),
    )

    root_node = feishu["root_node_token"]

    # 第一步：删除所有子节点
    logger.info("[STEP 1] 删除飞书上的所有内容...")
    children = client.list_child_nodes(root_node)

    if not children:
        logger.info("  根节点下没有子节点，跳过删除")
    else:
        logger.info("  找到 %d 个子节点", len(children))
        for child in children:
            node_token = child.get("node_token", "")
            title = child.get("title", "未知")
            logger.info("  删除: %s (%s)", title, node_token)

            if not dry_run:
                if _delete_wiki_node(feishu['space_id'], node_token):
                    logger.info("    已删除")
                else:
                    logger.error("    删除失败")
            else:
                logger.info("    [DRY-RUN] 将删除")

    # 第二步：重新同步
    logger.info("[STEP 2] 重新同步所有笔记...")
    run_sync(dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NoteForge 本地笔记 → 飞书知识库同步",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印同步计划，不执行 API 调用",
    )
    parser.add_argument(
        "--file",
        help="只同步包含此关键词的文件（如 '第01集'）",
    )
    parser.add_argument(
        "--category",
        help="只同步指定分类（如 '课程笔记'）",
    )
    parser.add_argument(
        "--new-only", action="store_true",
        help="只同步新增文件（跳过已存在的）",
    )
    parser.add_argument(
        "--renumber", action="store_true",
        help="仅重编号（不修改内容），修复序号乱序/重复",
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="删除飞书上的所有内容后重新同步（危险操作！）",
    )
    parser.add_argument(
        "--clean-confirm", action="store_true",
        help="确认删除（必须与 --clean 一起使用）",
    )
    args = parser.parse_args()

    try:
        # 如果是清理模式
        if args.clean:
            if not args.clean_confirm:
                logger.error("使用 --clean 必须同时指定 --clean-confirm")
                logger.info("示例: python -m noteforge.integration.feishu_sync --clean --clean-confirm")
                sys.exit(1)
            clean_and_resync(dry_run=args.dry_run)
        elif args.renumber:
            run_renumber(dry_run=args.dry_run, category_filter=args.category)
        else:
            run_sync(
                dry_run=args.dry_run,
                file_filter=args.file,
                category_filter=args.category,
                new_only=args.new_only,
            )
    except KeyboardInterrupt:
        logger.warning("用户中断，同步中止")
        sys.exit(130)
    except Exception as e:
        logger.error("同步失败: %s", e)
        import traceback
        traceback.print_exc()
        sys.exit(1)


def run_renumber(dry_run: bool = False, category_filter: Optional[str] = None) -> None:
    """仅重编号飞书笔记，不修改内容。用于修复序号乱序/重复。"""
    _load_env_file()
    config = _load_config()
    feishu = _get_feishu_config(config)

    print("=" * 60)
    print("  NoteForge → 飞书笔记重编号")
    print(f"  模式: {'DRY-RUN' if dry_run else '正式重编号'}")
    if category_filter:
        print(f"  过滤: 分类 '{category_filter}'")
    print("=" * 60)

    client = FeishuClient(
        space_id=feishu["space_id"],
        dry_run=dry_run,
        api_interval=feishu.get("api_interval", 0.5),
    )

    root_node = feishu["root_node_token"]
    categories = feishu.get("categories", [])
    groups, _ = scan_notes()

    total_renamed = 0
    for cat_config in categories:
        cat_name = cat_config.get("name", "")
        if category_filter and category_filter not in cat_name:
            continue
        if cat_name == "其他笔记":
            continue

        # 找到分类节点
        cat_token = _find_category_token(client, root_node, cat_name)
        if not cat_token:
            logger.warning("未找到分类: %s", cat_name)
            continue

        # 找到逐集笔记子节点
        sub_nodes = client.list_child_nodes(cat_token)
        for sub in sub_nodes:
            sub_title = sub.get("title", "").replace("📁 ", "").replace("📄 ", "").strip()
            sub_token = sub.get("node_token", "")
            if "逐集" not in sub_title:
                continue
            # 构建与 scan_notes() 一致的路径（不带 emoji）
            sub_path = f"{cat_name}/{sub_title}"
            files = groups.get(sub_path, [])
            if files:
                file_paths = [f for _, f in files]
                print(f"\n  {sub_path}: {len(file_paths)} 篇")
                _renumber_category(client, sub_token, file_paths)
                total_renamed += 1
            else:
                logger.warning("  分类 %s 下没有找到笔记文件", sub_path)

    print(f"\n  重编号完成，处理 {total_renamed} 个分类")


def _find_category_token(client: FeishuClient, root_token: str, cat_name: str) -> Optional[str]:
    """在根节点下查找分类的 node_token（兼容带 emoji 前缀的标题）"""
    children = client.list_child_nodes(root_token)
    for child in children:
        title = child.get("title", "")
        # 去掉 emoji 前缀（如 "📁 "）后比较
        clean = title.replace("📁 ", "").replace("📄 ", "").strip()
        if clean == cat_name:
            return child.get("node_token", "")
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
    main()
