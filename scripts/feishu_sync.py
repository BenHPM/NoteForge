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
import os
import sys
from pathlib import Path
from typing import Optional

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "video-to-text" / "scripts"
CONFIG_PATH = PROJECT_ROOT / "video-to-text" / "config" / "llm_engine_config.yaml"

# 添加 scripts 目录到 path 以 import feishu_client
sys.path.insert(0, str(SCRIPTS_DIR))

# Windows 控制台编码修复
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from feishu_client import FeishuClient, md_to_blocks, yaml_to_doc_blocks, match_category


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
        print(f"\033[31m[ERROR]\033[0m 配置文件不存在: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_feishu_config(config: dict) -> dict:
    """从配置中提取 feishu 段，校验必填项。"""
    feishu = config.get("feishu", {})
    if not feishu.get("enabled", False):
        print("\033[33m[WARN]\033[0m 飞书同步未启用（feishu.enabled = false）")
        print("  请在 config/llm_engine_config.yaml 中设置 feishu.enabled: true")
        sys.exit(0)
    required = ["space_id", "root_node_token"]
    for key in required:
        if not feishu.get(key):
            print(f"\033[31m[ERROR]\033[0m 缺少配置项: feishu.{key}")
            sys.exit(1)
    return feishu


def scan_notes() -> tuple[dict[str, list[tuple[str, Path]]], set[str]]:
    """
    扫描笔记文件并按 categories 分组。

    Returns:
        (groups, matched_files):
        - groups: {category_name: [(文件名, 文件路径), ...]}
        - matched_files: 已匹配的文件名集合（用于排除兜底分类重复匹配）
    """
    config = _load_config()
    feishu = config.get("feishu", {})
    categories = feishu.get("categories", [])
    exclude_patterns = feishu.get("exclude_patterns", [])
    notes_dir = PROJECT_ROOT / "video-to-text" / "output" / "notes"

    groups: dict[str, list[tuple[str, Path]]] = {}
    matched_files: set[str] = set()  # 跟踪已匹配的文件

    if not notes_dir.exists():
        print(f"\033[33m[WARN]\033[0m 笔记目录不存在: {notes_dir}")
        return groups, matched_files

    # 收集所有文件
    all_files: list[tuple[str, Path]] = []
    for md_file in sorted(notes_dir.glob("*.md")):
        filename = md_file.name

        # 检查是否在排除列表中
        if any(fnmatch.fnmatch(filename, pattern) for pattern in exclude_patterns):
            print(f"  [SKIP] 排除文件: {filename}")
            continue

        all_files.append((filename, md_file))

    # 扫描 spnr 目录（如果存在）
    spnr_file = PROJECT_ROOT / "spnr" / "nr" / "视频笔记.md"
    if spnr_file.exists():
        filename = spnr_file.name
        if not any(fnmatch.fnmatch(filename, pattern) for pattern in exclude_patterns):
            all_files.append((filename, spnr_file))

    # 按配置的分类顺序匹配（先匹配的优先）
    for cat_config in categories:
        cat_name = cat_config.get("name", "")
        children_config = cat_config.get("children", [])
        fallback_pattern = cat_config.get("pattern")

        if children_config:
            # 父子层级分类
            for child_config in children_config:
                child_pattern = child_config.get("pattern", "")
                for filename, filepath in all_files:
                    if filename not in matched_files and fnmatch.fnmatch(filename, child_pattern):
                        if cat_name not in groups:
                            groups[cat_name] = []
                        groups[cat_name].append((filename, filepath))
                        matched_files.add(filename)
        elif fallback_pattern:
            # 兜底分类（只匹配未被其他分类匹配的文件）
            for filename, filepath in all_files:
                if filename not in matched_files and fnmatch.fnmatch(filename, fallback_pattern):
                    if cat_name not in groups:
                        groups[cat_name] = []
                    groups[cat_name].append((filename, filepath))
                    matched_files.add(filename)

    total = sum(len(files) for files in groups.values())
    print(f"\033[32m[INFO]\033[0m 扫描到 {total} 个笔记文件，分为 {len(groups)} 个分类")
    for cat, files in groups.items():
        print(f"  - {cat}: {len(files)} 篇")

    return groups, matched_files


def run_sync(
    dry_run: bool = False,
    file_filter: Optional[str] = None,
    category_filter: Optional[str] = None,
    new_only: bool = False,
) -> None:
    """执行同步流程（支持父子层级结构）。"""
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

    # 初始化客户端（通过 lark-cli，用户身份）
    client = FeishuClient(
        space_id=feishu["space_id"],
        block_batch_size=feishu.get("block_batch_size", 50),
        dry_run=dry_run,
    )

    root_node = feishu["root_node_token"]
    categories = feishu.get("categories", [])

    # 扫描并分组
    groups, matched_files = scan_notes()

    # 过滤分类
    if category_filter:
        if category_filter not in groups:
            print(f"\033[31m[ERROR]\033[0m 未找到分类: {category_filter}")
            print(f"  可用分类: {', '.join(groups.keys())}")
            sys.exit(1)
        groups = {category_filter: groups[category_filter]}

    # 执行同步
    synced = 0
    skipped = 0
    errors = 0

    for cat_config in categories:
        cat_name = cat_config.get("name", "")
        children_config = cat_config.get("children", [])
        fallback_pattern = cat_config.get("pattern")

        # 如果是兜底分类（有 pattern 但没有 children）
        if fallback_pattern:
            # 收集所有未匹配的文件
            matched_files = []
            for filename, filepath in groups.get("其他笔记", []):
                if file_filter and file_filter not in filename:
                    continue
                matched_files.append((filename, filepath))

            if matched_files:
                print(f"\n\033[36m[STEP]\033[0m 同步分类: {cat_name} ({len(matched_files)} 篇)")
                parent_node = client.ensure_category_node(root_node, cat_name)

                for idx, (filename, filepath) in enumerate(matched_files, 1):
                    title = filepath.stem
                    print(f"  [{idx}/{len(matched_files)}] {title}")

                    try:
                        content = filepath.read_text(encoding="utf-8")
                    except Exception as e:
                        print(f"    \033[31m[ERROR]\033[0m 读取失败: {e}")
                        errors += 1
                        continue

                    blocks = md_to_blocks(content)
                    print(f"    解析得到 {len(blocks)} 个 block")

                    existing = client.find_node_by_title(parent_node, title)
                    if existing:
                        if new_only:
                            print(f"    已存在，跳过（--new-only）")
                            skipped += 1
                            continue
                        obj_token = existing.get("obj_token") or existing.get("node_token", "")
                        print(f"    已存在，更新内容...")
                        try:
                            client.overwrite_document(obj_token, blocks)
                            print(f"    \033[32m[OK]\033[0m 已更新")
                            synced += 1
                        except Exception as e:
                            print(f"    \033[31m[ERROR]\033[0m 更新失败: {e}")
                            errors += 1
                    else:
                        try:
                            client.create_document_and_write(parent_node, title, blocks)
                            print(f"    \033[32m[OK]\033[0m 已创建")
                            synced += 1
                        except Exception as e:
                            print(f"    \033[31m[ERROR]\033[0m 创建失败: {e}")
                            errors += 1
            continue

        # 如果是父子层级分类（有 children）
        if children_config:
            print(f"\n\033[36m[STEP]\033[0m 同步分类: {cat_name}")

            # 创建父节点
            parent_node = client.ensure_category_node(root_node, cat_name)
            print(f"  父节点: {cat_name}")

            # 按 order 排序 children_config
            children_config_sorted = sorted(children_config, key=lambda x: x.get("order", 99))

            for child_config in children_config_sorted:
                child_pattern = child_config.get("pattern", "")
                child_title = child_config.get("node_title")

                # 找到匹配的文件
                matched_files = []
                for group_name, files in groups.items():
                    for filename, filepath in files:
                        if fnmatch.fnmatch(filename, child_pattern):
                            if file_filter and file_filter not in filename:
                                continue
                            matched_files.append((filename, filepath))

                if not matched_files:
                    continue

                # 如果 child_title 为 null，每个文件创建为独立子节点
                if child_title is None:
                    for idx, (filename, filepath) in enumerate(matched_files, 1):
                        title = filepath.stem
                        print(f"  [{idx}/{len(matched_files)}] {title}")

                        try:
                            content = filepath.read_text(encoding="utf-8")
                        except Exception as e:
                            print(f"    \033[31m[ERROR]\033[0m 读取失败: {e}")
                            errors += 1
                            continue

                        blocks = md_to_blocks(content)
                        print(f"    解析得到 {len(blocks)} 个 block")

                        existing = client.find_node_by_title(parent_node, title)
                        if existing:
                            if new_only:
                                print(f"    已存在，跳过（--new-only）")
                                skipped += 1
                                continue
                            obj_token = existing.get("obj_token") or existing.get("node_token", "")
                            print(f"    已存在，更新内容...")
                            try:
                                client.overwrite_document(obj_token, blocks)
                                print(f"    \033[32m[OK]\033[0m 已更新")
                                synced += 1
                            except Exception as e:
                                print(f"    \033[31m[ERROR]\033[0m 更新失败: {e}")
                                errors += 1
                        else:
                            try:
                                client.create_document_and_write(parent_node, title, blocks)
                                print(f"    \033[32m[OK]\033[0m 已创建")
                                synced += 1
                            except Exception as e:
                                print(f"    \033[31m[ERROR]\033[0m 创建失败: {e}")
                                errors += 1
                else:
                    # 如果有 child_title，所有文件合并到一个子节点
                    print(f"  子节点: {child_title} ({len(matched_files)} 篇)")

                    # 合并所有文件内容
                    all_blocks = []
                    for filename, filepath in matched_files:
                        try:
                            content = filepath.read_text(encoding="utf-8")
                            blocks = md_to_blocks(content)
                            all_blocks.extend(blocks)
                        except Exception as e:
                            print(f"    \033[31m[ERROR]\033[0m 读取失败 {filename}: {e}")
                            errors += 1

                    if all_blocks:
                        existing = client.find_node_by_title(parent_node, child_title)
                        if existing:
                            if new_only:
                                print(f"    已存在，跳过（--new-only）")
                                skipped += 1
                                continue
                            obj_token = existing.get("obj_token") or existing.get("node_token", "")
                            print(f"    已存在，更新内容...")
                            try:
                                client.overwrite_document(obj_token, all_blocks)
                                print(f"    \033[32m[OK]\033[0m 已更新")
                                synced += 1
                            except Exception as e:
                                print(f"    \033[31m[ERROR]\033[0m 更新失败: {e}")
                                errors += 1
                        else:
                            try:
                                client.create_document_and_write(parent_node, child_title, all_blocks)
                                print(f"    \033[32m[OK]\033[0m 已创建")
                                synced += 1
                            except Exception as e:
                                print(f"    \033[31m[ERROR]\033[0m 创建失败: {e}")
                                errors += 1

    # 汇总
    print("\n" + "=" * 60)
    print(f"  同步完成！")
    print(f"  知识库 space_id: {feishu['space_id']}")
    print(f"  同步: {synced} | 跳过: {skipped} | 错误: {errors}")
    print("=" * 60)


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
                print("\033[31m[ERROR]\033[0m 使用 --clean 必须同时指定 --clean-confirm")
                print("  示例: python feishu_sync.py --clean --clean-confirm")
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
        print("\n\033[33m[WARN]\033[0m 用户中断，同步中止")
        sys.exit(130)
    except Exception as e:
        print(f"\n\033[31m[ERROR]\033[0m 同步失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def clean_and_resync(dry_run: bool = False) -> None:
    """删除飞书上的所有内容后重新同步。"""
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
    )

    root_node = feishu["root_node_token"]

    # 第一步：删除所有子节点
    print("\n\033[36m[STEP 1]\033[0m 删除飞书上的所有内容...")
    children = client.list_child_nodes(root_node)

    if not children:
        print("  根节点下没有子节点，跳过删除")
    else:
        print(f"  找到 {len(children)} 个子节点")
        for child in children:
            node_token = child.get("node_token", "")
            title = child.get("title", "未知")
            print(f"  删除: {title} ({node_token})")

            if not dry_run:
                try:
                    client._api(
                        "DELETE",
                        f"wiki/v2/spaces/{feishu['space_id']}/nodes/{node_token}",
                    )
                    print(f"    \033[32m[OK]\033[0m 已删除")
                except Exception as e:
                    print(f"    \033[31m[ERROR]\033[0m 删除失败: {e}")
            else:
                print(f"    [DRY-RUN] 将删除")

    # 第二步：重新同步
    print("\n\033[36m[STEP 2]\033[0m 重新同步所有笔记...")
    run_sync(dry_run=dry_run)


if __name__ == "__main__":
    main()
