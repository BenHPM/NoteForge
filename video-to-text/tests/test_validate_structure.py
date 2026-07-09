"""
NoteFormatter.validate_structure 笔记结构校验单元测试

覆盖：
  - lecture / tutorial / interview / podcast 合法结构
  - meeting 模式（决策+待办）
  - 缺少标题报错
  - 缺少二级标题报错

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_validate_structure.py -v
"""
import os
import pytest

os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


class TestValidateStructure:
    """测试 note_formatter.validate_structure 各内容类型"""

    def setup_method(self):
        from noteforge.core.note_formatter import NoteFormatter
        self.formatter = NoteFormatter()

    def _make_note(self, title="测试笔记", content_type='lecture', **extra):
        """构造基本的合法笔记"""
        parts = [f"# {title}"]
        if content_type == 'lecture':
            parts.append("**课程定位**: 测试课程")
        parts.append("## 核心观点")
        parts.append("- 观点1")
        parts.append("- 观点2")
        parts.append("## 学习总结")
        parts.append("总结内容")
        parts.append("- [ ] 行动项1")
        if content_type in ('lecture', 'tutorial'):
            parts.append('> "金句内容"')
        parts.append("笔记整理时间: 2026-01-01")
        parts.append("学习来源: 测试来源")
        return "\n".join(parts)

    def test_valid_lecture_note(self):
        """合法 lecture 笔记应无结构问题"""
        note = self._make_note(content_type='lecture')
        issues = self.formatter.validate_structure(note, content_type='lecture')
        assert len(issues) == 0, f"合法笔记不应有结构问题: {issues}"

    def test_valid_tutorial_note(self):
        """合法 tutorial 笔记应无结构问题"""
        note = self._make_note(content_type='tutorial')
        issues = self.formatter.validate_structure(note, content_type='tutorial')
        assert len(issues) == 0, f"合法笔记不应有结构问题: {issues}"

    def test_valid_interview_note(self):
        """合法 interview 笔记应无结构问题"""
        note = self._make_note(content_type='interview')
        issues = self.formatter.validate_structure(note, content_type='interview')
        assert len(issues) == 0, f"合法笔记不应有结构问题: {issues}"

    def test_valid_podcast_note(self):
        """合法 podcast 笔记应无结构问题"""
        note = self._make_note(content_type='podcast')
        issues = self.formatter.validate_structure(note, content_type='podcast')
        assert len(issues) == 0, f"合法笔记不应有结构问题: {issues}"

    def test_meeting_note_structure(self):
        """meeting 笔记应检查决策或待办"""
        note = "# 会议纪要\n## 讨论内容\n- 决策: 采纳方案A\n- 待办: 下周完成报告"
        issues = self.formatter.validate_structure(note, mode='meeting', content_type='meeting')
        assert len(issues) == 0, f"合法会议纪要不应有结构问题: {issues}"

    def test_missing_title(self):
        """缺少标题应报错"""
        note = "## 核心观点\n- 观点1\n## 学习总结\n总结"
        issues = self.formatter.validate_structure(note, content_type='lecture')
        assert any('标题' in i for i in issues), "缺少标题应报错"

    def test_missing_h2(self):
        """缺少二级标题应报错"""
        note = "# 测试笔记\n核心观点\n学习总结"
        issues = self.formatter.validate_structure(note, content_type='lecture')
        assert len(issues) > 0, "缺少二级标题应报错"
