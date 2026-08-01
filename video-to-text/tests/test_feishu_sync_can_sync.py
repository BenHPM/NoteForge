# -*- coding: utf-8 -*-
"""
feishu_sync can_sync() 独立验证测试

覆盖：
  - 正常笔记通过验证
  - 过短内容被阻止
  - 拒绝文本被检测
  - 上游 LLM_REFUSAL_DETECTED 标记被检测
  - 缺少结构节被阻止
  - 实质内容行过少被阻止
"""

import pytest
from noteforge.integration.feishu_sync import can_sync


class TestCanSync:
    """can_sync 独立验证测试"""

    def _make_normal_note(self) -> str:
        """生成一个正常笔记（应通过验证）"""
        return (
            "# 测试笔记\n\n"
            "> **课程定位**：测试用\n\n"
            "---\n\n"
            "## 核心观点\n\n"
            "这是一个测试笔记，包含足够的内容来通过验证。\n"
            "笔记中有多个段落和实质内容，确保长度和结构都满足要求。\n"
            "第三行内容确保实质内容行数足够。\n\n"
            "## 学习总结\n\n"
            "总结内容在这里，包含核心收获和行动清单。\n\n"
            "- [ ] 测试行动项 — 每天 | 产出测试结果\n\n"
            "---\n\n"
            "*笔记整理时间：2026-08-01*\n"
            "*学习来源：原视频音频转写*\n"
        )

    def test_normal_note_passes(self):
        content = self._make_normal_note()
        can, reasons = can_sync(content, "test.md")
        assert can is True
        assert reasons == []

    def test_too_short_content_blocked(self):
        """过短内容应被阻止"""
        content = "# 短笔记\n\n只有一行内容。"
        can, reasons = can_sync(content, "short.md")
        assert can is False
        assert any("过短" in r for r in reasons)

    def test_refusal_text_detected(self):
        """LLM 拒绝文本应被检测"""
        content = self._make_normal_note()
        # 在笔记中插入拒绝文本
        content = content.replace(
            "这是一个测试笔记",
            "I cannot complete this request as an AI language model",
        )
        can, reasons = can_sync(content, "refusal.md")
        assert can is False
        assert any("拒绝文本" in r for r in reasons)

    def test_refusal_marker_detected(self):
        """上游 LLM_REFUSAL_DETECTED 标记应被检测"""
        content = self._make_normal_note()
        content += "\n\n⚠️ **LLM_REFUSAL_DETECTED** — 此笔记包含 LLM 拒绝文本。"
        can, reasons = can_sync(content, "marked.md")
        assert can is False
        assert any("LLM_REFUSAL_DETECTED" in r for r in reasons)

    def test_no_sections_blocked(self):
        """缺少二级标题节应被阻止"""
        content = (
            "# 无结构笔记\n\n"
            "这是一段很长的内容，但是没有任何二级标题结构。"
            "虽然字数足够，但缺少结构化节标记。"
            "继续添加内容以确保长度超过 100 字。"
            "再添加一些内容来确保长度足够。"
            "最后再加一行确保长度。"
        )
        can, reasons = can_sync(content, "no_sections.md")
        assert can is False
        assert any("二级标题" in r for r in reasons)

    def test_only_headers_blocked(self):
        """只有标题没有实质内容应被阻止"""
        content = (
            "# 只有标题\n\n"
            "## 第一节\n\n"
            "## 第二节\n\n"
            "## 第三节\n\n"
            "---\n\n"
            "*笔记整理时间：2026-08-01*\n"
        )
        can, reasons = can_sync(content, "headers_only.md")
        assert can is False
        # 应该有实质内容行过少的问题
        assert any("实质内容行过少" in r for r in reasons)

    def test_chinese_refusal_detected(self):
        """中文拒绝文本应被检测"""
        content = self._make_normal_note()
        content = content.replace(
            "这是一个测试笔记",
            "作为人工智能语言模型，我无法完成此请求",
        )
        can, reasons = can_sync(content, "cn_refusal.md")
        assert can is False
        assert any("拒绝文本" in r for r in reasons)

    def test_empty_content_blocked(self):
        """空内容应被阻止"""
        can, reasons = can_sync("", "empty.md")
        assert can is False
        assert len(reasons) > 0

    def test_multiple_issues_reported(self):
        """多个问题应同时报告"""
        content = "# 短\n\nI cannot generate this."
        can, reasons = can_sync(content, "multi.md")
        assert can is False
        # 应至少报告过短 + 拒绝文本
        assert len(reasons) >= 2
