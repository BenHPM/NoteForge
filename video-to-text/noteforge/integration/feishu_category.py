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
