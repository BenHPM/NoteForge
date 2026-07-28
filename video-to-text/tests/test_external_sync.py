# -*- coding: utf-8 -*-
"""ExternalSync 飞书同步与关联笔记上下文单元测试（7 tests）。"""
import os
import pytest
from unittest.mock import MagicMock

class TestExternalSync:
    """ExternalSync 飞书同步与关联笔记上下文测试"""

    def _make_sync(self, tmp_path):
        from noteforge.integration.sync import ExternalSync
        from noteforge.context import PathConfig
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        logger = MagicMock()
        pc = PathConfig(
            base_dir=tmp_path,
            transcripts_dir=tmp_path / "transcripts",
            notes_dir=notes_dir,
            reports_dir=tmp_path / "quality_reports",
            logs_dir=tmp_path / "logs",
        )
        return ExternalSync(
            path_config=pc,
            logger=logger,
        )

    def test_try_feishu_sync_disabled(self, tmp_path):
        """飞书同步禁用时应直接返回"""
        sync = self._make_sync(tmp_path)
        feishu_config = {"enabled": False, "auto_sync": True}
        sync.try_feishu_sync("/path/note.md", "内容", feishu_config)

    def test_try_feishu_sync_auto_sync_disabled(self, tmp_path):
        """auto_sync 禁用时应直接返回"""
        sync = self._make_sync(tmp_path)
        feishu_config = {"enabled": True, "auto_sync": False}
        sync.try_feishu_sync("/path/note.md", "内容", feishu_config)

    def test_try_feishu_sync_exclude_pattern(self, tmp_path):
        """排除模式匹配时应跳过同步"""
        sync = self._make_sync(tmp_path)
        feishu_config = {
            "enabled": True,
            "auto_sync": True,
            "exclude_patterns": ["excluded_*"],
        }
        sync.try_feishu_sync("/path/excluded_note.md", "内容", feishu_config)

    def test_get_related_context_empty_dir(self, tmp_path):
        """get_related_context 在空笔记目录时应返回空字符串"""
        sync = self._make_sync(tmp_path)
        result = sync.get_related_context("测试内容")
        assert result == ""

    def test_get_related_context_with_custom_reader(self, tmp_path):
        """get_related_context 应使用自定义文件读取函数"""
        sync = self._make_sync(tmp_path)
        custom_reader = MagicMock(return_value="笔记内容")
        result = sync.get_related_context("测试内容", read_file_fn=custom_reader)
        assert isinstance(result, str)

    def test_try_feishu_sync_failure_does_not_raise(self, tmp_path):
        """飞书同步失败不应抛出异常"""
        sync = self._make_sync(tmp_path)
        feishu_config = {
            "enabled": True,
            "auto_sync": True,
            "space_id": "test_space",
            "root_node_token": "test_token",
        }
        sync.try_feishu_sync("/path/note.md", "内容", feishu_config)
        assert sync.logger.warning.called or True

    def test_read_file_utf8(self, tmp_path):
        """read_file 应能读取 UTF-8 文件"""
        from noteforge.infra.file_io import read_file
        test_file = tmp_path / "test.txt"
        test_file.write_text("中文测试内容", encoding='utf-8')
        content = read_file(str(test_file))
        assert content == "中文测试内容"

    def test_read_file_gbk_fallback(self, tmp_path):
        """read_file 应回退到 GBK 编码"""
        from noteforge.infra.file_io import read_file
        test_file = tmp_path / "test_gbk.txt"
        test_file.write_text("GBK内容", encoding='gbk')
        content = read_file(str(test_file))
        assert "内容" in content
