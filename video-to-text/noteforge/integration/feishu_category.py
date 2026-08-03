"""
feishu_category.py — 文件名 → 飞书分类匹配

负责将文件名映射到飞书知识库的二级分类。

导入路径：
  from noteforge.integration.feishu_category import match_category

也可通过：
  from noteforge.integration.feishu import match_category
"""

import fnmatch
from typing import Optional


def match_category(filename: str, categories: list[dict]) -> str:
    """按文件名匹配二级分类。支持两种格式：
    - 新格式: {name, match: ["pattern1", "pattern2"], exclude: ["pat", ...]}
    - 旧格式: {name, children: [...]} 或 {pattern: "...", node_title: "..."}

    匹配策略（与 feishu_sync._match_leaf 完全一致，保证两条同步路径
    --CLI 批量同步 vs 笔记生成后自动同步--给出相同结果）：
      1. 按配置顺序遍历分类，第一个 match 命中的胜出
      2. 命中 match 后检查 exclude：命中任一 exclude 模式则跳过此分类，
         让后续更合适的分类匹配（防跨域截胡）
      3. 所有分类都未命中 -> "其他笔记"（兜底）

    稳定性保证：纯文件名 fnmatch，不读内容、不依赖 LLM，
    任何运行环境（强/弱模型、有/无 LLM）下对同一文件名给出同一结果。
    """
    for cat in categories:
        name = cat.get("name", cat.get("node_title", ""))
        patterns = cat.get("match", [])
        exclude_pats = cat.get("exclude", [])
        matched = False
        for pat in patterns:
            if fnmatch.fnmatch(filename, pat):
                matched = True
                break
        if not matched:
            # 旧格式兼容：单个 pattern
            if "pattern" in cat and fnmatch.fnmatch(filename, cat["pattern"]):
                matched = True
        if matched:
            # exclude 检查：命中任一排除模式则跳过此分类，留给后续分类
            if exclude_pats and any(
                fnmatch.fnmatch(filename, ep) for ep in exclude_pats
            ):
                continue
            return name
    return "其他笔记"
