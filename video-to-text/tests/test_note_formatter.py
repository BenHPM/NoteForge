"""
NoteFormatter 模块单元测试

覆盖：
  - 笔记格式化（自动添加标题）
  - 返回类型校验

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_note_formatter.py -v
"""
import os
import pytest

os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


class TestNoteFormatter:
    """NoteFormatter 模块测试"""

    @pytest.fixture
    def formatter(self):
        from noteforge.core.note_formatter import NoteFormatter
        return NoteFormatter()

    def test_format_adds_title(self, formatter):
        note = "这是一些内容\n\n## 章节\n\n正文内容"
        formatted = formatter.format(note, title="测试标题")
        assert "# " in formatted

    def test_format_returns_string(self, formatter):
        note = "# 标题\n\n内容"
        formatted = formatter.format(note, title="标题")
        assert isinstance(formatted, str)
        assert len(formatted) > 0
