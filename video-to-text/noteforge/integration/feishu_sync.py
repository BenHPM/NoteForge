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
import time
from datetime import datetime
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
from noteforge.core.note_value import is_low_value_note


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

    # 6. 低价值内容检测（招生简章/上线通知/无知识可提炼）— 同步前最后防线
    if is_low_value_note(filename, content):
        reasons.append("低价值内容（招生/上线通知/无知识可提炼，本地保留但不同步）")

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
    action: str        # "created" / "updated" / "skipped" / "partial" / "failed"
    node_token: str
    category: str       # 所属分类路径，如 "金融投资/逐集笔记"
    cat_node_token: str  # 分类父节点 token（用于生成分类链接）
    error: str = ""     # Risk-3: 失败/部分失败时的错误信息


# Risk-3: 半同步状态追踪
# 飞书 API 批量写入时可能部分成功（如 50 个 block 中前 30 个写入成功，后 20 个失败）。
# 此状态文件记录"半同步"文档，下次同步时优先重试。
_PARTIAL_SYNC_FILE = BASE_DIR / "output" / "logs" / ".partial_sync.json"


def _load_partial_sync() -> dict:
    """加载半同步状态文件"""
    if _PARTIAL_SYNC_FILE.exists():
        try:
            return json.loads(_PARTIAL_SYNC_FILE.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_partial_sync(data: dict) -> None:
    """保存半同步状态文件"""
    _PARTIAL_SYNC_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PARTIAL_SYNC_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), 'utf-8'
    )


def _mark_partial(path_key: str, title: str, error: str) -> None:
    """标记文档为半同步状态（部分写入成功）

    Args:
        path_key: 分类路径/标题（与 hash_cache 同 key）
        title: 文档标题
        error: 错误信息
    """
    partial = _load_partial_sync()
    partial[path_key] = {
        'title': title,
        'error': error[:200],
        'ts': datetime.now().isoformat() if 'datetime' in dir() else '',
        'retry_count': partial.get(path_key, {}).get('retry_count', 0) + 1,
    }
    _save_partial_sync(partial)
    logger.warning(f"半同步标记: {title} — {error[:80]}")


def _clear_partial(path_key: str) -> None:
    """清除文档的半同步标记（同步成功后调用）"""
    partial = _load_partial_sync()
    if path_key in partial:
        del partial[path_key]
        _save_partial_sync(partial)


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


def _build_child_index(client: FeishuClient, parent_token: str) -> dict:
    """P0.2: 一次性拉取子节点并建立索引，避免每篇笔记都调 list_child_nodes。

    原实现每篇笔记最多 4 次 list_child_nodes（精确/display/base/clean），
    192 篇约 768 次调用。改为每子分类调 1 次 + 内存匹配，约 16 次。

    Returns:
        {"children": [...], "by_title": {title: child}, "by_clean": {clean_title: child}}
        by_title/by_clean 用 setdefault 保留 API 顺序中的第一个（与原遍历行为一致）
    """
    children = client.list_child_nodes(parent_token)
    by_title: dict[str, dict] = {}
    by_clean: dict[str, dict] = {}
    for child in children:
        title = child.get("title", "")
        if title:
            by_title.setdefault(title, child)
            by_clean.setdefault(_clean_title(title), child)
    return {"children": children, "by_title": by_title, "by_clean": by_clean}


def _find_existing_in_index(
    index: dict, title: str, display_title: str, should_index: bool,
) -> Optional[dict]:
    """P0.2: 在预建索引中多策略查找已存在节点（零 API 调用）。

    查找顺序与原 find_node_by_title 多次调用等价：
    1. 精确标题匹配
    2. 带序号的 display_title（逐集笔记）
    3. 去序号的 base title
    4. clean_title 匹配（飞书节点可能带不同前缀序号）
    """
    by_title = index["by_title"]
    existing = by_title.get(title)
    if existing:
        return existing
    if should_index:
        existing = by_title.get(display_title)
        if existing:
            return existing
    base = re.sub(r'^\d+\.\s*', '', title)
    if base != title:
        existing = by_title.get(base)
        if existing:
            return existing
    return index["by_clean"].get(title)


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
        # 删除时重试 + 间隔，避免连续调用触发限流导致静默失败（曾出现 86 次删除中 27 次静默失败）
        remaining = list(duplicates_to_delete)
        for token, title in duplicates_to_delete:
            logger.info(f"    删除重复: {title}")
            ok = False
            for _attempt in range(3):
                try:
                    if _delete_wiki_node(client.space_id, token):
                        ok = True
                        remaining.remove((token, title))
                        break
                except Exception as e:
                    logger.debug(f"    删除异常: {e}")
                time.sleep(1.0)
            if not ok:
                logger.warning(f"    删除失败（3 次重试后仍失败）: {title}")
        # 从列表中仅移除确认已删除的节点（删除失败的节点保留，避免重编号把残留重复计入）
        if remaining:
            remaining_set = set(remaining)
            deleted = [d for d in duplicates_to_delete if d not in remaining_set]
            ordered_nodes = [(t, tl) for t, tl in ordered_nodes if (t, tl) not in deleted]
        else:
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
    junk_patterns = feishu.get("junk_patterns", [])
    notes_dir = BASE_DIR / "output" / "notes"

    groups: dict[str, list[tuple[str, Path]]] = {}
    matched_files: set[str] = set()

    if not notes_dir.exists():
        logger.warning("笔记目录不存在: %s", notes_dir)
        return groups, matched_files

    def _skip_reason(filename: str, filepath: Path) -> Optional[str]:
        """返回应跳过同步的原因（排除模式 / 低价值），否则 None"""
        if any(fnmatch.fnmatch(filename, p) for p in exclude_patterns):
            return "排除"
        try:
            preview = filepath.read_text(encoding="utf-8")[:3000]
        except OSError:
            preview = ""
        if is_low_value_note(filename, preview, junk_patterns):
            return "低价值"
        return None

    # 收集所有文件（排除模式 + 低价值笔记，本地保留但不同步到飞书）
    all_files: list[tuple[str, Path]] = []
    for md_file in sorted(notes_dir.glob("*.md")):
        filename = md_file.name
        reason = _skip_reason(filename, md_file)
        if reason:
            logger.info("%s文件（不同步）: %s", reason, filename)
            continue
        all_files.append((filename, md_file))

    # 扫描 spnr 目录（如果存在）
    spnr_file = PROJECT_ROOT / "spnr" / "nr" / "视频笔记.md"
    if spnr_file.exists():
        filename = spnr_file.name
        if not _skip_reason(filename, spnr_file):
            all_files.append((filename, spnr_file))

    def _match_leaf(node: dict, path: str) -> None:
        """匹配二级分类 + 内部固定子结构。
        - 普通分类：跨集提炼 / 逐集笔记（跨集在上）
        - 其他笔记：无子结构，直接平铺（暂存池）
        支持新格式 (match 列表 + 可选 exclude) 和旧格式 (pattern/children)。

        exclude 规则：文件名命中 exclude 中任一模式时，跳过该分类，
        让后续更合适的分类来匹配（防跨域截胡）。
        """
        patterns = node.get("match", [])
        exclude_pats = node.get("exclude", [])
        if patterns:
            is_other = node.get("name", "") == "其他笔记"
            for filename, filepath in all_files:
                if filename not in matched_files:
                    for pat in patterns:
                        if fnmatch.fnmatch(filename, pat):
                            # exclude 检查：命中任一排除模式则跳过此分类
                            if exclude_pats and any(
                                fnmatch.fnmatch(filename, ep) for ep in exclude_pats
                            ):
                                logger.debug(
                                    "exclude 拦截: %s 被 %s 排除，留给后续分类",
                                    filename, node.get("name", ""),
                                )
                                break  # 跳出 patterns 循环，不匹配此分类
                            if is_other:
                                # 其他笔记：无子结构，直接平铺
                                target_path = path
                            # 合成文档判定：仅「知识体系/跨集/提炼」标记进跨集提炼。
                            # 不匹配 *模型*/*框架*（访谈/讲座标题常含"模型""框架"，属逐集笔记）。
                            elif any(fnmatch.fnmatch(filename, sp) for sp in
                                     ["*知识体系*", "*跨集*", "*提炼*"]):
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


def detect_pending_categories(
    groups: dict[str, list[tuple[str, Path]]],
    knowledge_domains: list[dict],
    notes_dir: Path,
    threshold: int = 5,
) -> dict[str, list[str]]:
    """检测「其他笔记」暂存池中待提升为独立分类的同类笔记。

    确定性聚类（不依赖 LLM）：
      1. 只扫描「其他笔记」分组（暂存池）
      2. 对每个文件做两级匹配：
         a. match_files：文件名 fnmatch（如 *AI*、*访谈*）
         b. match_keywords：文件名 + 内容前 5000 字关键词命中
      3. 同一知识域命中数 >= threshold（默认 5）时，标记为待提升

    设计原则：这是纯确定性逻辑，与 LLM 能力无关。无论使用强/弱模型，
    只要文件名和内容相同，聚类结果就完全一致。封装 CLI/API 的价值
    正在于把这类分类决策固化为可复现、可测试的规则，而非依赖模型判断。

    Args:
        groups: scan_notes() 的返回值（叶子分类路径 -> 文件列表）
        knowledge_domains: knowledge_domains 配置（含 id/match_files/match_keywords）
        notes_dir: 笔记目录（用于读取内容做关键词匹配）
        threshold: 触发独立分类的同类笔记数下限

    Returns:
        {domain_id: [filename, ...]}，命中数 >= threshold 的域及其文件
    """
    other_files = groups.get("其他笔记", [])
    if not other_files:
        return {}

    # 建立 domain_id -> 配置 的索引（跳过 general 兜底域）
    domain_cfg = {d["id"]: d for d in knowledge_domains if d.get("id") != "general"}
    if not domain_cfg:
        return {}

    import fnmatch as _fnmatch

    def _matches_domain(filename: str, filepath: Path, dcfg: dict) -> bool:
        try:
            content = filepath.read_text(encoding="utf-8")[:5000].lower()
        except OSError:
            content = ""
        name_lower = filename.lower()

        # 0. 排除词检查（文件名 + 内容），命中则不算该域
        for kw in (dcfg.get("exclude_keywords", []) or []):
            if kw.lower() in name_lower or kw.lower() in content:
                return False

        # 1. match_files 文件名匹配（最高优先级）
        match_files = dcfg.get("match_files", []) or []
        if match_files and any(_fnmatch.fnmatch(filename, pat) for pat in match_files):
            return True
        # 2. match_keywords 内容匹配
        keywords = dcfg.get("match_keywords", []) or []
        if not keywords:
            return False
        hits = sum(1 for kw in keywords if kw.lower() in name_lower
                   or kw.lower() in content)
        if hits == 0:
            return False
        # 归一化命中占比，避免大关键词列表域占优
        return hits / len(keywords) >= 0.2

    # 统计暂存池各域命中
    domain_hits: dict[str, list[str]] = {}
    for filename, filepath in other_files:
        for d_id, dcfg in domain_cfg.items():
            if _matches_domain(filename, filepath, dcfg):
                domain_hits.setdefault(d_id, []).append(filename)
                break  # 每个文件只归属第一个命中的域

    # 过滤低于阈值的域
    return {d_id: files for d_id, files in domain_hits.items()
            if len(files) >= threshold}


def promote_pending_categories(
    client,
    groups: dict,
    pending: dict,
    domains: list,
    categories: list,
    root_node: str,
    file_filter: Optional[str],
    new_only: bool,
    dry_run: bool,
    sync_items: list,
    hash_cache: dict,
) -> tuple[int, int, int, int]:
    """将暂存池中待提升的同类笔记自动独立成分类（确定性，不依赖 LLM）。

    行为：
      1. 对每个待提升域（cluster ≥ threshold），分类名 = knowledge_domains.name
      2. 该分类名已存在于 feishu.categories 配置：跳过自动创建，仅提示。
         既有分类 match 过窄属配置问题，不应自动改 match/exclude 引发跨域截胡。
      3. 全新分类：文件从「其他笔记」移出到 {分类}/跨集提炼 与 {分类}/逐集笔记，
         调用 _sync_node 创建节点并同步，最后持久化新分类到配置。
      4. dry_run：只报告待提升项，不移动文件、不创建节点、不改配置。

    Returns:
        (promoted, synced, skipped, errors)
    """
    promoted = synced = skipped = errors = 0
    existing_names = {c.get("name", "") for c in categories}
    other_path = "其他笔记"
    other_files = groups.get(other_path, [])

    for d_id, filenames in pending.items():
        dcfg = next((d for d in domains if d.get("id") == d_id), {})
        cat_name = dcfg.get("name", d_id)
        if not cat_name:
            continue
        if cat_name in existing_names:
            logger.info(
                "[暂存池自动分类] %s 已是配置分类（match 过窄漏接 %d 篇），跳过自动创建",
                cat_name, len(filenames),
            )
            continue

        logger.info("[暂存池自动分类] 提升 %s: %d 篇", cat_name, len(filenames))
        promoted += 1
        if dry_run:
            continue

        # 移动文件：其他笔记 -> {分类}/跨集提炼 与 {分类}/逐集笔记
        fname_set = set(filenames)
        remaining: list = []
        moved: list = []
        for f in other_files:
            (remaining if f[0] not in fname_set else moved).append(f)
        other_files = remaining

        # 合成文档判定：仅「知识体系/跨集/提炼」标记进跨集提炼（排除标题含"模型/框架"的逐集笔记）
        _synth_pats = ["*知识体系*", "*跨集*", "*提炼*"]
        cross_names = {f[0] for f in moved if any(
            fnmatch.fnmatch(f[0], sp) for sp in _synth_pats
        )}
        cross_files = [f for f in moved if f[0] in cross_names]
        ep_files = [f for f in moved if f[0] not in cross_names]

        if cross_files:
            groups[f"{cat_name}/跨集提炼"] = cross_files
        if ep_files:
            groups[f"{cat_name}/逐集笔记"] = ep_files
        groups[other_path] = other_files

        # 创建节点并同步（match=["*"] 仅用于进入二级分类分支，文件按 groups 路径读取）
        synthetic_cat = {"name": cat_name, "match": ["*"], "exclude": []}
        s, sk, e = _sync_node(
            client, synthetic_cat, root_node, groups, cat_name,
            file_filter, new_only, dry_run, sync_items, hash_cache,
        )
        synced += s
        skipped += sk
        errors += e

        # 持久化新分类（确定性 match/exclude，供后续扫描直接命中）
        match_pats = list(dcfg.get("match_files", []) or [])
        excl_pats = [f"*{kw}*" for kw in (dcfg.get("exclude_keywords", []) or [])]
        if _persist_category_config(cat_name, match_pats, excl_pats):
            logger.info("[暂存池自动分类] 已持久化分类 %s 到配置", cat_name)

    return promoted, synced, skipped, errors


def _persist_category_config(
    cat_name: str,
    match_patterns: list,
    exclude_patterns: Optional[list] = None,
) -> bool:
    """将新分类持久化到 feishu.categories（纯文本插入，保留注释与行尾）。

    分类名已存在时跳过（不覆盖既有 match/exclude，避免破坏人工调优的结构）。
    插入位置：「其他笔记」条目之前（保持其他笔记始终最后）。

    Args:
        cat_name: 分类名
        match_patterns: fnmatch 模式列表（来自 knowledge_domains.match_files）
        exclude_patterns: fnmatch 排除模式列表（来自 exclude_keywords → *kw*）

    Returns:
        是否成功写入
    """
    if not CONFIG_PATH.exists():
        logger.warning("配置文件不存在，无法持久化分类: %s", CONFIG_PATH)
        return False

    raw = CONFIG_PATH.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"

    # 分类名已存在则跳过
    name_pat = re.compile(
        r'^    - name:\s*["\']?%s["\']?\s*$' % re.escape(cat_name), re.M,
    )
    if name_pat.search(raw):
        logger.debug("[持久化] 分类 %s 已存在配置，跳过", cat_name)
        return False

    anchor = '    - name: "其他笔记"'
    if anchor not in raw:
        logger.warning("[持久化] 找不到「其他笔记」锚点，无法插入 %s", cat_name)
        return False

    def _fmt_list(items):
        return "[" + ", ".join(f'"{p}"' for p in items) + "]"

    block = (
        f'    - name: "{cat_name}"{newline}'
        f'      match: {_fmt_list(match_patterns)}{newline}'
    )
    if exclude_patterns:
        block += f'      exclude: {_fmt_list(exclude_patterns)}{newline}'

    idx = raw.index(anchor)
    CONFIG_PATH.write_text(raw[:idx] + block + raw[idx:], encoding="utf-8")
    logger.info("[持久化] 已写入分类 %s 到 %s", cat_name, CONFIG_PATH.name)
    return True


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
                    if not sub_token:
                        logger.error("  %s子节点 %s 创建失败，跳过该分类", indent, sub_name)
                        continue
                    sub_nodes_to_sync.append((sub_token, files, sub_path))

        for sub_token, files, sub_path in sub_nodes_to_sync:
            indent = "  " + "  " if not is_other else "  "
            is_synth = "跨集" in sub_path
            # P0.2: 每个子分类只 list 一次，建立索引后内存匹配（原每篇笔记最多 4 次 list）
            child_index = _build_child_index(client, sub_token) if not dry_run else None
            for idx, (filename, filepath) in enumerate(files, 1):
                if file_filter and file_filter not in filename:
                    continue

                # 稳定标题：纯文件名 stem（去 _v5），不带序号
                title = _clean_title(filepath.stem)
                # 逐集笔记在创建时直接带序号，避免后续 PATCH 重命名失败
                should_index = not is_synth and not is_other
                display_title = f"{idx}. {title}" if should_index else title
                logger.info("  %s[%d/%d] %s", indent, idx, len(files), display_title)

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
                        "  %s  同步验证未通过: %s",
                        indent, '; '.join(sync_reasons),
                    )
                    errors += 1
                    continue

                blocks = md_to_blocks(content)
                logger.info("  %s  解析得到 %d 个 block", indent, len(blocks))

                cache_key = f"{path}/{title}"
                if dry_run:
                    existing = None
                else:
                    # P0.2: 在预建索引中多策略查找（零 API 调用），等价于原 4 次 find_node_by_title
                    existing = _find_existing_in_index(
                        child_index, title, display_title, should_index,
                    )
                if existing:
                    if new_only:
                        logger.info("  %s  已存在，跳过（--new-only）", indent)
                        skipped += 1
                        sync_items.append(SyncItem(display_title, "skipped",
                            existing.get("node_token", ""), path, sub_token))
                        continue

                    # 先处理标题序号不匹配（如 junk 笔记占位导致整体错位）：删除旧文档后按新序号重建，
                    # 优先于 hash 跳过，否则错号节点永远不会被修正
                    existing_title = existing.get("title", "")
                    needs_recreate = should_index and existing_title != display_title
                    if needs_recreate:
                        content_hash = _content_hash(content)
                        old_node_token = existing.get("node_token", "")
                        try:
                            obj_token = client.create_document_and_write(
                                sub_token, display_title, blocks,
                            )
                            if old_node_token:
                                try:
                                    _delete_wiki_node(client.space_id, old_node_token)
                                except Exception as del_err:
                                    logger.warning(
                                        "  %s  旧文档删除失败（下次同步将清理）: %s",
                                        indent, del_err,
                                    )
                            logger.info("  %s  已重建（序号修正）", indent)
                            synced += 1
                            hash_cache[cache_key] = content_hash
                            _clear_partial(cache_key)  # Risk-3: 清除半同步标记
                            sync_items.append(SyncItem(display_title, "created",
                                obj_token or "", path, sub_token))
                        except Exception as e:
                            logger.error("  %s  重建失败: %s", indent, e)
                            errors += 1
                            _mark_partial(cache_key, display_title, str(e))  # Risk-3
                        continue

                    # 内容 hash 比对，未变化则跳过
                    content_hash = _content_hash(content)
                    if hash_cache.get(cache_key) == content_hash:
                        logger.info("  %s  内容未变化，跳过", indent)
                        skipped += 1
                        sync_items.append(SyncItem(display_title, "skipped",
                            existing.get("node_token", ""), path, sub_token))
                        continue

                    # 更新已有文档
                    try:
                        doc_token = existing.get("obj_token", "")
                        client.overwrite_document(doc_token, blocks)
                        logger.info("  %s  已更新", indent)
                        synced += 1
                        hash_cache[cache_key] = content_hash
                        _clear_partial(cache_key)  # Risk-3: 清除半同步标记
                        sync_items.append(SyncItem(display_title, "updated",
                            existing.get("node_token", ""), path, sub_token))
                    except Exception as e:
                        logger.error("  %s  更新失败: %s", indent, e)
                        errors += 1
                        _mark_partial(cache_key, display_title, str(e))  # Risk-3
                else:
                    # 创建新文档
                    try:
                        obj_token = client.create_document_and_write(
                            sub_token, display_title, blocks,
                        )
                        logger.info("  %s  已创建", indent)
                        synced += 1
                        hash_cache[cache_key] = _content_hash(content)
                        _clear_partial(cache_key)  # Risk-3: 清除半同步标记
                        sync_items.append(SyncItem(display_title, "created",
                            obj_token or "", path, sub_token))
                    except Exception as e:
                        logger.error("  %s  创建失败: %s", indent, e)
                        errors += 1
                        _mark_partial(cache_key, display_title, str(e))  # Risk-3

            # 同步完成后自动重编号（仅逐集笔记，非跨集提炼）
            # 创建时已带序号，此步骤作为兜底/顺序校正
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

        # P0.2: 每个叶子分类只 list 一次，建立索引后内存匹配
        child_index = _build_child_index(client, node_token) if not dry_run else None

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

            # P0.2: 索引内存匹配（叶子节点无序号，should_index=False）
            existing = _find_existing_in_index(child_index, title, title, False) if not dry_run else None
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
                    _clear_partial(cache_key)  # Risk-3: 清除半同步标记
                    sync_items.append(SyncItem(title, "updated",
                        existing.get("node_token", ""), path, node_token))
                except Exception as e:
                    logger.error("  %s  更新失败: %s", indent, e)
                    errors += 1
                    _mark_partial(cache_key, title, str(e))  # Risk-3: 标记半同步
            else:
                try:
                    obj_token = client.create_document_and_write(node_token, title, blocks)
                    logger.info("  %s  已创建", indent)
                    synced += 1
                    _clear_partial(f"{path}/{title}")  # Risk-3: 清除半同步标记
                    sync_items.append(SyncItem(title, "created",
                        obj_token or "", path, node_token))
                except Exception as e:
                    logger.error("  %s  创建失败: %s", indent, e)
                    errors += 1
                    _mark_partial(f"{path}/{title}", title, str(e))  # Risk-3: 标记半同步

    return synced, skipped, errors


def _collect_all_titles(client: FeishuClient, node_token: str) -> set[str]:
    """递归收集节点下所有文档标题（叶子节点）。

    P0.1 优化：优先用 list_child_nodes 返回的 has_child 字段判断是否文件夹，
    避免对每个节点再调一次 list_child_nodes（原实现 8 分类约 880 次 API 调用，
    现降至约 16 次）。has_child 字段缺失时回退到逐节点 list（兼容旧 API）。
    """
    titles = set()
    children = client.list_child_nodes(node_token)
    for child in children:
        title = child.get("title", "")
        child_token = child.get("node_token", "")
        # 飞书 API 中 obj_type 对文件夹和文档都是 "docx"
        # 优先用 has_child 字段判断是否文件夹（零 API 成本）
        has_child = child.get("has_child")
        if has_child is None and child_token:
            # 字段缺失（极旧 API），回退到逐节点 list
            has_child = bool(client.list_child_nodes(child_token))
        if child_token and has_child:
            # 文件夹节点，递归
            titles.update(_collect_all_titles(client, child_token))
        elif title:
            # 叶子文档节点
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

    # 收集其他分类中所有文档标题（P0.1 后 has_child 字段使递归调用数从 ~880 降到 ~16）
    classified_titles: set[str] = set()
    logger.info("  收集已分类文档标题（%d 个分类）...", len(category_nodes))
    for token, name in category_nodes:
        classified_titles.update(_collect_all_titles(client, token))
    logger.info("  已收集 %d 个已分类标题", len(classified_titles))

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


def _check_token_expiry() -> None:
    """P1.6: 检查 lark-cli user token 有效期，refresh token <2 天时提醒重新授权。

    飞书 OAuth 的 refresh token 约 7 天过期，这是"每次成功后过一段时间又需处理"
    的主因。提前提醒把"莫名其妙失败"变成"明确告知该重新授权"。
    """
    try:
        lark_path = _find_lark_cli()
        r = subprocess.run(
            [lark_path, "auth", "status"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15,
            stdin=subprocess.DEVNULL,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return
        data = json.loads(r.stdout)
        user = data.get("identities", {}).get("user", {})
        if user.get("status") != "ready":
            print("[警告] lark-cli 用户身份未就绪，可能需重新授权: lark-cli auth login --as user")
            return
        refresh_expires = user.get("refreshExpiresAt", "")
        if not refresh_expires:
            return
        # 解析 ISO 8601（如 "2026-08-16T20:37:30+08:00"）
        dt = datetime.fromisoformat(refresh_expires)
        now = datetime.now(dt.tzinfo)
        remaining = dt - now
        if remaining.days < 2:
            print(f"[警告] 飞书授权将在 {remaining.days} 天后过期，请尽快运行: lark-cli auth login --as user")
        else:
            print(f"[预检] 授权有效，剩余 {remaining.days} 天")
    except Exception as e:
        logger.debug(f"token 有效期检查失败（非致命）: {e}")


def _preflight(client: FeishuClient, root_node: str) -> None:
    """P1.4: 同步前预检--lark-cli 配置 + 授权 + 知识库可达。

    失败时打印明确指令并退出，避免同步中途 cryptic 失败（如 not_configured）。
    dry_run 模式跳过（不发起真实 API 调用）。
    """
    if client.dry_run:
        return
    print("\n[预检] 验证飞书连接...")
    try:
        client.list_child_nodes(root_node)
    except RuntimeError as e:
        err = str(e).lower()
        if "not configured" in err or "not_configured" in err:
            print("\n[预检失败] lark-cli 未配置。请运行：")
            print("  lark-cli config init --new")
            print("  （按提示在浏览器完成应用授权，或设置 HERMES_HOME 环境变量）")
        elif "unauthorized" in err or "invalid authentication" in err or "99991663" in err or ("token" in err and "invalid" in err):
            print("\n[预检失败] 飞书授权无效或已过期。请运行：")
            print("  lark-cli auth login --as user")
        else:
            print(f"\n[预检失败] 无法访问飞书知识库: {e}")
            print("  请检查网络连接，或运行 lark-cli doctor 排查")
        sys.exit(1)
    except Exception as e:
        print(f"\n[预检失败] 未知错误: {e}")
        sys.exit(1)
    _check_token_expiry()
    print("[预检] 通过\n")


def run_sync(
    dry_run: bool = False,
    file_filter: Optional[str] = None,
    category_filter: Optional[str] = None,
    new_only: bool = False,
) -> None:
    """执行同步流程（支持多级嵌套结构）。"""
    _load_env_file()  # 加载 .env 环境变量
    # P0.3: 管道/重定向输出时强制行缓冲，避免长时间无输出看似挂起
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass
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
    # P1.4: 同步前预检（配置/授权/网络），失败给明确指令而非 cryptic 中断
    _preflight(client, root_node)

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

    # 暂存池自动分类（确定性，不依赖 LLM）：同域笔记 ≥ threshold 时自动独立成类
    auto_promote_cfg = feishu.get("auto_promote", {})
    auto_enabled = auto_promote_cfg.get("enabled", True)
    auto_threshold = int(auto_promote_cfg.get("threshold", 5))
    domains = config.get("knowledge_domains", [])
    notes_dir = BASE_DIR / "output" / "notes"
    pending: dict = {}
    if auto_enabled and not category_filter:
        pending = detect_pending_categories(
            groups, domains, notes_dir, auto_threshold,
        )
        if pending:
            print(f"\n[暂存池自动分类] 检测到待提升分类（≥{auto_threshold} 篇）:")
            for d_id, files in sorted(pending.items()):
                dcfg = next((d for d in domains if d.get("id") == d_id), {})
                print(f"  {dcfg.get('name', d_id)}: {len(files)} 篇")
            promoted, s, sk, e = promote_pending_categories(
                client, groups, pending, domains, categories, root_node,
                file_filter, new_only, dry_run, sync_items, hash_cache,
            )
            synced += s
            skipped += sk
            errors += e
        else:
            print("\n[暂存池自动分类] 无待提升分类")

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
    # Risk-3: 报告半同步状态
    partial = _load_partial_sync()
    if partial:
        print(f"  [警告] 半同步文档: {len(partial)} 篇（部分写入成功，下次同步将重试）")
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
    # P1.4: 清理前预检（清理是高危操作，更应先确认连接正常）
    _preflight(client, root_node)

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
    # P1.5: 统一日志（控制台 + noteforge.log 文件），便于事后排查同步问题
    from noteforge.infra.logging_setup import setup_logging
    setup_logging(log_dir=BASE_DIR / "output" / "logs")
    main()
