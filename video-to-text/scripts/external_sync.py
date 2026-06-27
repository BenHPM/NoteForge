# -*- coding: utf-8 -*-
"""
NoteForge 外部同步模块
提取自 llm_note_engine.py 的飞书同步与关联笔记上下文逻辑
"""

import os
from pathlib import Path
from typing import Optional


class ExternalSync:
    """飞书同步与关联笔记上下文处理器"""

    def __init__(self, base_dir, notes_dir, logger):
        """
        Args:
            base_dir: 项目根目录 (Path)
            notes_dir: 笔记输出目录 (Path)
            logger: 日志记录器
        """
        self._base_dir = base_dir
        self._notes_dir = notes_dir
        self.logger = logger
        self._knowledge_index = None  # 缓存 KnowledgeIndex

    def try_feishu_sync(self, output_path: str, note_text: str,
                        feishu_config: dict) -> None:
        """
        尝试将笔记同步到飞书知识库。失败只 warn，不影响主流程。

        Args:
            output_path: 笔记输出路径
            note_text: 笔记文本内容
            feishu_config: 飞书配置字典（来自 config['feishu']）
        """
        if not feishu_config.get("enabled", False):
            return
        if not feishu_config.get("auto_sync", False):
            return

        # 检查排除模式
        from fnmatch import fnmatch
        filename = Path(output_path).name
        exclude_patterns = feishu_config.get("exclude_patterns", [])
        if any(fnmatch(filename, pat) for pat in exclude_patterns):
            self.logger.info(f"飞书同步跳过（匹配排除模式）: {filename}")
            return

        try:
            from feishu_client import FeishuClient, md_to_blocks, match_category

            client = FeishuClient(
                space_id=feishu_config.get("space_id") or os.environ.get("FEISHU_SPACE_ID", ""),
                block_batch_size=feishu_config.get("block_batch_size", 50),
                api_interval=feishu_config.get("api_interval", 0.5),
            )

            # 按文件名匹配分类
            filename = Path(output_path).name
            categories = feishu_config.get("categories", [])
            category = match_category(filename, categories)

            # 确保分类节点存在
            root_node = feishu_config["root_node_token"]
            category_node = client.ensure_category_node(root_node, category)

            # 转换并同步
            blocks = md_to_blocks(note_text)
            title = Path(output_path).stem

            existing = client.find_node_by_title(category_node, title)
            if existing:
                obj_token = existing.get("obj_token") or existing.get("node_token", "")
                client.overwrite_document(obj_token, blocks)
                self.logger.info(f"已更新飞书文档: {title}")
            else:
                client.create_document_and_write(category_node, title, blocks)
                self.logger.info(f"已同步到飞书: {title}")

        except Exception as e:
            self.logger.warning(f"飞书同步失败（不影响笔记生成）: {e}")

    def get_related_context(self, content: str, limit: int = 3,
                            read_file_fn=None) -> str:
        """
        获取与当前内容相关的已有笔记上下文

        Args:
            content: 当前转写文本
            limit: 关联笔记数量上限
            read_file_fn: 文件读取回调，签名 (path) -> str。
                          默认使用内置 _read_file。

        Returns:
            格式化的上下文文本，或空字符串
        """
        if read_file_fn is None:
            read_file_fn = self._read_file

        try:
            from knowledge_index import KnowledgeIndex
            # 缓存 KnowledgeIndex，避免批量时 O(n²) 重建
            if self._knowledge_index is None:
                self._knowledge_index = KnowledgeIndex(str(self._notes_dir))
            idx = self._knowledge_index
            related = idx.find_related_notes(content, limit=limit)

            if not related:
                return ""

            parts = ["## 相关历史笔记（供参考，不要重复这些内容）\n"]
            for path, score in related:
                try:
                    note_text = read_file_fn(path)
                    stem = Path(path).stem
                    # 取前 2000 字作为摘要
                    summary = note_text[:2000]
                    if len(note_text) > 2000:
                        summary += "\n...(已截断)"
                    parts.append(f"### {stem} (相关度: {score:.0%})\n\n{summary}")
                except Exception:
                    continue

            return "\n\n".join(parts)
        except Exception as e:
            self.logger.debug(f"获取关联笔记失败: {e}")
            return ""

    @staticmethod
    def _read_file(path: str) -> str:
        """读取文件（尝试 UTF-8，回退 GBK）"""
        for encoding in ('utf-8', 'gbk', 'gb2312'):
            try:
                with open(path, 'r', encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法读取文件（编码问题）: {path}")
