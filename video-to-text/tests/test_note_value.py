# -*- coding: utf-8 -*-
"""
NoteForge 低价值笔记检测单元测试

覆盖 noteforge/core/note_value.py 的 is_low_value_note：
  - 文件名标记（简章/招生/公告/预告/报名/宣传片）
  - 内容标记（无法生成结构化学习笔记/招生宣传文案/上线通知）
  - 配置化文件名模式（junk_patterns fnmatch）
  - 边界：有干货的宣传标题（如上架讲座）不误杀
"""
import pytest

from noteforge.core.note_value import (
    is_low_value_note,
    LOW_VALUE_CONTENT_MARKERS,
    LOW_VALUE_FILENAME_MARKERS,
)


class TestIsLowValueNote:

    def test_filename_简章(self):
        assert is_low_value_note("中国人民大学2024年春季在职研究生项目简章.md") is True

    def test_filename_招生(self):
        assert is_low_value_note("XX学院招生宣传.md") is True

    def test_filename_上线通知(self):
        assert is_low_value_note("《课程》B站上线通知.md") is True

    def test_content_marker_无法生成(self):
        content = "基于当前文本，无法生成符合用户要求（提取分析方法）的结构化学习笔记。"
        assert is_low_value_note("人大简章.md", content) is True

    def test_content_marker_招生宣传(self):
        content = "这是一段中国人民大学国际关系学院的招生宣传文案，目的在于招生推广。"
        assert is_low_value_note("某学院介绍.md", content) is True

    def test_content_marker_上线通知(self):
        content = "# 《人民币汇率与人民币国际化》课程上线通知"
        assert is_low_value_note("课程介绍.md", content) is True

    def test_extra_filename_patterns(self):
        """配置化 junk_patterns 应生效，且不误伤未命中文件"""
        assert is_low_value_note(
            "某课程预告片.md", "", extra_filename_patterns=["*预告*"]
        ) is True
        assert is_low_value_note(
            "某课程介绍.md", "", extra_filename_patterns=["*上线啦*"]
        ) is False

    def test_junk_patterns_上架讲座不误杀(self):
        """有干货的新书/讲座「上架啦」不应被文件名过滤，除非内容自述纯宣传"""
        content = (
            "## 核心观点\n中美经贸关系正经历系统性重构。\n"
            "## 分析框架\n变量识别框架：宏观时代变量、国内发展变量。"
        )
        # 文件名含"上架啦"但配置模式只含"上线啦"，且内容有干货 → 不拦截
        assert is_low_value_note("翟东升新书《缠斗》上架啦！.md", content) is False

    def test_normal_note_not_low_value(self):
        content = "## 核心观点\n这是正常讲座笔记，包含完整的知识提炼和行动指引。"
        assert is_low_value_note("正常讲座.md", content) is False

    def test_empty_content_no_filename_hit(self):
        assert is_low_value_note("普通笔记.md", "") is False

    def test_marker_lists_nonempty(self):
        assert len(LOW_VALUE_CONTENT_MARKERS) > 0
        assert len(LOW_VALUE_FILENAME_MARKERS) > 0
