#!/usr/bin/env python3
"""
feishu_sync.py — NoteForge 本地笔记 → 飞书知识库同步脚本（薄包装）

功能：
  1. 读取配置文件（llm_engine_config.yaml 的 feishu 段）
  2. 扫描本地笔记文件，按 categories 规则分组
  3. 调用 FeishuClient 执行同步

用法：
  python feishu_sync.py                    # 同步所有笔记
  python feishu_sync.py --dry-run          # 预览模式
  python feishu_sync.py --file "第01集.md" # 同步单个文件
  python feishu_sync.py --category "课程笔记" # 同步某个分类
  python feishu_sync.py --new-only         # 只同步新增（跳过已存在的）

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


def _load_hash_cache() -> dict:
    if HASH_CACHE_FILE.exists():
        return json.loads(HASH_CACHE_FILE.read_text(encoding='utf-8'))
    return {}


def _save_hash_cache(cache: dict):
    HASH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    HASH_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), 'utf-8')


def _content_hash(content: str) -> str:
    return hashlib.md5(content.encode('utf-8')).hexdigest()[:12]


class SyncItem(NamedTuple):
    """单篇笔记的同步结果"""
    title: str
    action: str        # "created" / "updated" / "skipped"
    node_token: str
    category: str       # 所属分类路径，如 "金融投资/逐集笔记"
    cat_node_token: str  # 分类父节点 token（用于生成分类链接）

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "video-to-text" / "config" / "llm_engine_config.yaml"
HASH_CACHE_FILE = PROJECT_ROOT / "video-to-text" / "output" / "logs" / ".sync_hash_cache.json"

from noteforge.integration.feishu import FeishuClient, md_to_blocks, match_category


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
    import yaml
    if not CONFIG_PATH.exists():
        logger.error("配置文件不存在: %s", CONFIG_PATH)
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_feishu_config(config: dict) -> dict:
    """从配置中提取 feishu 段，支持环境变量回退。"""
    feishu = config.get("feishu", {})
    if not feishu.get("enabled", False):
        logger.warning("飞书同步未启用（feishu.enabled = false）")
        logger.info("请在 config/llm_engine_config.yaml 中设置 feishu.enabled: true")
        sys.exit(0)
    # 环境变量回退：YAML 留空时从环境变量读取
    if not feishu.get("space_id"):
        feishu["space_id"] = os.environ.get("FEISHU_SPACE_ID", "")
    if not feishu.get("root_node_token"):
        feishu["root_node_token"] = os.environ.get("FEISHU_ROOT_NODE_TOKEN", "")
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
    notes_dir = PROJECT_ROOT / "video-to-text" / "output" / "notes"

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

                title = filepath.stem
                if title.endswith('_v5'):
                    title = title[:-3]
                # 逐集笔记自动加序号，跨集提炼 / 已有序号的不加
                if not is_synth and not re.match(r'^第?\s*\d+\s*[集章]', title):
                    title = f"{idx}. {title}"
                logger.info("  %s[%d/%d] %s", indent, idx, len(files), title)

                try:
                    content = filepath.read_text(encoding="utf-8")
                except Exception as e:
                    logger.error("  %s  读取失败: %s", indent, e)
                    errors += 1
                    continue

                blocks = md_to_blocks(content)
                logger.info("  %s  解析得到 %d 个 block", indent, len(blocks))

                existing = client.find_node_by_title(sub_token, title)
                # 带序号后找不到时，尝试不带序号的旧标题（兼容老数据）
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

            title = filepath.stem
            if title.endswith('_v5'):
                title = title[:-3]
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
    """查找 lark-cli 路径"""
    import shutil
    for name in ("lark-cli", "lark-cli.exe"):
        path = shutil.which(name)
        if path:
            return path
    # Windows 常见路径
    for p in [
        os.path.expandvars(r"%APPDATA%\npm\lark-cli.cmd"),
        os.path.expandvars(r"%APPDATA%\npm\lark-cli.exe"),
    ]:
        if os.path.exists(p):
            return p
    return "lark-cli"


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

    space_id = feishu.get("space_id", "")
    base_url = f"https://{FEISHU_WIKI_DOMAIN}/wiki"

    # 分类统计
    active = [i for i in sync_items if i.action in ("created", "updated")]
    skipped = [i for i in sync_items if i.action == "skipped"]

    if not active and cleaned == 0:
        return

    def is_synthesis(title: str) -> bool:
        return any(k in title for k in ['知识体系', '跨集', '提炼', '框架', '模型'])

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
                logger.info("示例: python feishu_sync.py --clean --clean-confirm")
                sys.exit(1)
            clean_and_resync(dry_run=args.dry_run)
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
    main()
