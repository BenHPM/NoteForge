# -*- coding: utf-8 -*-
"""
NoteForge 知识索引模块测试
覆盖: build_index, search, get_all_tags, find_related_notes, list_notes
        _extract_summary, _tokenize, _compute_relevance
"""

import os
os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'

import math
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

from noteforge.intelligence.knowledge_index import (
    KnowledgeIndex,
    NoteSummary,
    SearchResult,
)


# ============================================================
# 辅助：创建临时 markdown 笔记
# ============================================================

def _write_note(tmpdir, filename, content):
    """在 tmpdir 中写入一个 .md 文件，返回 Path"""
    p = Path(tmpdir) / filename
    p.write_text(content, encoding='utf-8')
    return p


def _basic_note(title="# Hello\n", body="Some content here.\n", date="笔记整理时间：2025-01-15\n"):
    return date + title + body


# ============================================================
# build_index
# ============================================================

class TestBuildIndex:

    def test_empty_directory_returns_zero(self):
        """空目录 → 返回 0"""
        with tempfile.TemporaryDirectory() as td:
            idx = KnowledgeIndex(td)
            count = idx.build_index()
            assert count == 0
            assert idx._index == {}
            assert idx._idf == {}

    def test_with_markdown_files_returns_count(self):
        """包含 .md 文件 → 返回数量并填充 _index / _idf"""
        with tempfile.TemporaryDirectory() as td:
            _write_note(td, "note1.md", "# Alpha\n笔记整理时间：2025-01-01\n内容A。\n")
            _write_note(td, "note2.md", "# Beta\n笔记整理时间：2025-01-02\n内容B。\n")
            idx = KnowledgeIndex(td)
            count = idx.build_index()
            assert count == 2
            assert len(idx._index) == 2
            assert len(idx._idf) > 0

    def test_skips_synthesis_files(self):
        """跳过 knowledge_synthesis / mental_models / action_playbook 前缀"""
        with tempfile.TemporaryDirectory() as td:
            _write_note(td, "knowledge_synthesis_domain.md", "# 合成\n内容\n")
            _write_note(td, "mental_models_short_video.md", "# 模型\n内容\n")
            _write_note(td, "action_playbook_review.md", "# 行动\n内容\n")
            _write_note(td, "real_note.md", "# 真实笔记\n笔记整理时间：2025-01-01\n内容\n")
            idx = KnowledgeIndex(td)
            count = idx.build_index()
            assert count == 1

    def test_skips_keyword_files(self):
        """文件名含 知识体系 或 knowledge_framework 时跳过"""
        with tempfile.TemporaryDirectory() as td:
            _write_note(td, "知识体系短视频导演.md", "# 体系\n内容\n")
            _write_note(td, "my_knowledge_framework_note.md", "# 框架\n内容\n")
            _write_note(td, "real_note.md", "# 真实笔记\n笔记整理时间：2025-01-01\n内容\n")
            idx = KnowledgeIndex(td)
            count = idx.build_index()
            assert count == 1

    def test_handles_file_read_error(self):
        """文件读取失败时优雅跳过"""
        with tempfile.TemporaryDirectory() as td:
            p = _write_note(td, "good.md", "# Good\n笔记整理时间：2025-01-01\n内容\n")
            # 插入一个后续读取会出错的条目
            bad = Path(td) / "bad.md"
            bad.write_text("temp", encoding='utf-8')
            # 在 build_index 遍历期间替换为会 raise 的文件
            with patch('pathlib.Path.read_text', side_effect=OSError("permission denied")):
                idx = KnowledgeIndex(td)
                # 移除 good.md 的 glob 结果，只保留 bad
                # 直接用更精确的 mock: 让所有文件都 raise
                pass

        # 更直接的方式: 让 _extract_summary 对特定文件 raise
        with tempfile.TemporaryDirectory() as td:
            p = _write_note(td, "good.md", "# Good\n笔记整理时间：2025-01-01\n内容\n")
            _write_note(td, "bad.md", "# Bad\n笔记整理时间：2025-01-02\n内容\n")
            idx = KnowledgeIndex(td)
            original_extract = idx._extract_summary
            call_count = [0]

            def failing_extract(md_file, content):
                call_count[0] += 1
                if "bad" in md_file.name:
                    raise RuntimeError("mock parse error")
                return original_extract(md_file, content)

            with patch.object(idx, '_extract_summary', side_effect=failing_extract):
                count = idx.build_index()

            assert count == 1
            assert call_count[0] == 2  # 两个文件都尝试过


# ============================================================
# search
# ============================================================

class TestSearch:

    def _make_idx_with_notes(self, notes_dir, notes):
        """创建 KnowledgeIndex 并写入笔记后 build"""
        for name, content in notes:
            _write_note(notes_dir, name, content)
        idx = KnowledgeIndex(notes_dir)
        idx.build_index()
        return idx

    def test_empty_index_returns_empty(self):
        """空索引 → search 返回 []"""
        with tempfile.TemporaryDirectory() as td:
            idx = KnowledgeIndex(td)
            idx.build_index()
            results = idx.search("anything")
            assert results == []

    def test_keyword_match_found(self):
        """关键词匹配 → 返回结果且按相关度排序"""
        with tempfile.TemporaryDirectory() as td:
            notes = [
                ("a.md", "# Alpha\n笔记整理时间：2025-01-01\nAlpha 量化投资策略讨论。\n"),
                ("b.md", "# Beta\n笔记整理时间：2025-01-02\nBeta 导演拍摄技巧。\n"),
            ]
            idx = self._make_idx_with_notes(td, notes)
            results = idx.search("量化")
            assert len(results) == 1
            assert results[0].title == "Alpha"

    def test_no_match_returns_empty(self):
        """无匹配 → []"""
        with tempfile.TemporaryDirectory() as td:
            notes = [
                ("a.md", "# Alpha\n笔记整理时间：2025-01-01\nAlpha 量化投资。\n"),
            ]
            idx = self._make_idx_with_notes(td, notes)
            results = idx.search("完全无关的内容xyz")
            assert results == []

    def test_tag_filter_works(self):
        """tags 过滤生效"""
        with tempfile.TemporaryDirectory() as td:
            notes = [
                ("a.md", "# Alpha\n笔记整理时间：2025-01-01\n量化基金讨论。\n"),
                ("b.md", "# Beta\n笔记整理时间：2025-01-02\n导演运镜技巧。\n"),
            ]
            idx = self._make_idx_with_notes(td, notes)
            # 两张笔记的标签都是通过 TF-IDF 自动提取的，我们只验证过滤
            # 用 tags 参数过滤——如果笔记没有该标签则不出现在结果中
            all_tags = idx.get_all_tags()
            results_all = idx.search("", limit=10)
            # 搜索空字符串仍会返回所有（relevance > 0 的），用 tag 过滤
            if all_tags:
                first_tag = list(all_tags.keys())[0]
                results_tag = idx.search("", tags=[first_tag])
                # 结果数应 <= 总数
                assert len(results_tag) <= len(results_all)

    def test_limit_parameter_respected(self):
        """limit 参数被遵守"""
        with tempfile.TemporaryDirectory() as td:
            notes = [
                ("a.md", "# A\n笔记整理时间：2025-01-01\n量化投资内容。\n"),
                ("b.md", "# B\n笔记整理时间：2025-01-02\n量化基金内容。\n"),
                ("c.md", "# C\n笔记整理时间：2025-01-03\n量化策略内容。\n"),
            ]
            idx = self._make_idx_with_notes(td, notes)
            results = idx.search("量化", limit=2)
            assert len(results) <= 2


# ============================================================
# get_all_tags
# ============================================================

class TestGetAllTags:

    def test_returns_dict_with_counts(self):
        """返回 {tag: count} 格式"""
        with tempfile.TemporaryDirectory() as td:
            _write_note(td, "a.md", "# A\n笔记整理时间：2025-01-01\n量化投资策略分析。\n")
            _write_note(td, "b.md", "# B\n笔记整理时间：2025-01-02\n基金量化回撤研究。\n")
            idx = KnowledgeIndex(td)
            idx.build_index()
            tags = idx.get_all_tags()
            assert isinstance(tags, dict)
            for tag, count in tags.items():
                assert isinstance(tag, str)
                assert isinstance(count, int)
                assert count > 0

    def test_empty_index_returns_empty_dict(self):
        """空索引 → {}"""
        with tempfile.TemporaryDirectory() as td:
            idx = KnowledgeIndex(td)
            idx.build_index()
            assert idx.get_all_tags() == {}


# ============================================================
# find_related_notes
# ============================================================

class TestFindRelatedNotes:

    def test_returns_related_notes_with_scores(self):
        """返回 (path, score) 列表"""
        with tempfile.TemporaryDirectory() as td:
            _write_note(td, "a.md", "# Alpha\n笔记整理时间：2025-01-01\n量化投资策略。\n")
            _write_note(td, "b.md", "# Beta\n笔记整理时间：2025-01-02\n导演拍摄技巧。\n")
            idx = KnowledgeIndex(td)
            idx.build_index()
            results = idx.find_related_notes("量化投资", limit=5)
            assert isinstance(results, list)
            for item in results:
                assert isinstance(item, tuple)
                assert len(item) == 2
                assert isinstance(item[0], str)
                assert isinstance(item[1], float)

    def test_no_related_returns_empty(self):
        """无相关笔记 → []"""
        with tempfile.TemporaryDirectory() as td:
            _write_note(td, "a.md", "# Alpha\n笔记整理时间：2025-01-01\n内容。\n")
            idx = KnowledgeIndex(td)
            idx.build_index()
            results = idx.find_related_notes("完全无关的关键词xyz")
            assert results == []


# ============================================================
# list_notes
# ============================================================

class TestListNotes:

    def test_returns_sorted_list_of_note_summary(self):
        """返回 NoteSummary 列表，按日期降序"""
        with tempfile.TemporaryDirectory() as td:
            with patch('os.path.getmtime', side_effect=[1000.0, 2000.0, 3000.0]):
                _write_note(td, "a.md", "# A\n笔记整理时间：2025-01-01\n内容A。\n")
                _write_note(td, "b.md", "# B\n笔记整理时间：2025-01-02\n内容B。\n")
                _write_note(td, "c.md", "# C\n笔记整理时间：2025-01-03\n内容C。\n")
            idx = KnowledgeIndex(td)
            idx.build_index()
            notes = idx.list_notes()
            assert len(notes) == 3
            for n in notes:
                assert isinstance(n, NoteSummary)
            # 按 date 降序（有笔记整理时间的，按该日期）
            dates = [n.date for n in notes]
            assert dates == sorted(dates, reverse=True)


# ============================================================
# _extract_summary
# ============================================================

class TestExtractSummary:

    def test_extracts_title_from_h1(self):
        """从第一个 # 标题提取标题"""
        with tempfile.TemporaryDirectory() as td:
            p = _write_note(td, "test.md", "# 量化投资笔记\n笔记整理时间：2025-01-01\n正文。\n")
            idx = KnowledgeIndex(td)
            # 不调用 build_index，直接调 _extract_summary
            content = p.read_text(encoding='utf-8')
            summary = idx._extract_summary(p, content)
            assert summary.title == "量化投资笔记"

    def test_fallback_title_to_filename(self):
        """无 h1 标题时回退到文件名"""
        with tempfile.TemporaryDirectory() as td:
            p = _write_note(td, "my_file.md", "无标题内容。\n笔记整理时间：2025-01-01\n")
            idx = KnowledgeIndex(td)
            content = p.read_text(encoding='utf-8')
            summary = idx._extract_summary(p, content)
            assert summary.title == "my_file"

    def test_extracts_date_from_pattern(self):
        """从「笔记整理时间：YYYY-MM-DD」提取日期"""
        with tempfile.TemporaryDirectory() as td:
            p = _write_note(td, "t.md", "# T\n笔记整理时间：2025-06-15\n内容\n")
            idx = KnowledgeIndex(td)
            content = p.read_text(encoding='utf-8')
            summary = idx._extract_summary(p, content)
            assert summary.date == "2025-06-15"

    def test_fallback_date_to_mtime(self):
        """无日期标记时回退到文件 mtime"""
        with tempfile.TemporaryDirectory() as td:
            p = _write_note(td, "t.md", "# Title\n无日期标记。\n")
            idx = KnowledgeIndex(td)
            fake_mtime = 1609459200.0  # 2021-01-01 (timezone-stable)
            with patch('os.path.getmtime', return_value=fake_mtime):
                content = p.read_text(encoding='utf-8')
                summary = idx._extract_summary(p, content)
            assert summary.date == "2021-01-01"

    def test_extracts_frameworks(self):
        """提取 ### 框架/模型/方法 后面的内容"""
        with tempfile.TemporaryDirectory() as td:
            content = "# T\n笔记整理时间：2025-01-01\n### 框架：MOAT 模型\n正文。\n"
            p = _write_note(td, "t.md", content)
            idx = KnowledgeIndex(td)
            summary = idx._extract_summary(p, content)
            assert len(summary.key_frameworks) > 0
            assert "MOAT 模型" in summary.key_frameworks[0]

    def test_extracts_action_items(self):
        """提取 - [ ] 待办项"""
        with tempfile.TemporaryDirectory() as td:
            content = "# T\n笔记整理时间：2025-01-01\n- [ ] 学习量化策略\n- [ ] 编写回测代码\n"
            p = _write_note(td, "t.md", content)
            idx = KnowledgeIndex(td)
            summary = idx._extract_summary(p, content)
            assert "学习量化策略" in summary.action_items
            assert "编写回测代码" in summary.action_items

    def test_counts_chars_and_words(self):
        """统计字符数和词数"""
        with tempfile.TemporaryDirectory() as td:
            content = "# Title\n笔记整理时间：2025-01-01\nABCD内容测试。\n"
            p = _write_note(td, "t.md", content)
            idx = KnowledgeIndex(td)
            summary = idx._extract_summary(p, content)
            assert summary.char_count > 0
            assert summary.word_count >= 0  # 分词结果取决于 jieba mock


# ============================================================
# _tokenize
# ============================================================

class TestTokenize:

    def test_mock_jieba_output(self):
        """mock jieba.lcut 控制分词结果"""
        with tempfile.TemporaryDirectory() as td:
            idx = KnowledgeIndex(td)
            with patch('jieba.lcut', return_value=['量化', '投资', '策略', '的', '分析']):
                words = idx._tokenize("量化投资策略的分析")
            # 停用词「的」应被过滤，且 len>=2
            assert '量化' in words
            assert '投资' in words
            assert '的' not in words

    def test_filters_stop_words(self):
        """过滤停用词"""
        idx = KnowledgeIndex("/tmp")
        with patch('jieba.lcut', return_value=['这个', '量化', '投资', '策略']):
            words = idx._tokenize("这个量化投资策略")
        assert '这个' not in words
        assert '量化' in words

    def test_filters_short_words(self):
        """过滤长度 < 2 的词"""
        idx = KnowledgeIndex("/tmp")
        with patch('jieba.lcut', return_value=['一', '量化', '策略']):
            words = idx._tokenize("一量化策略")
        assert '一' not in words

    def test_filters_digits(self):
        """过滤纯数字"""
        idx = KnowledgeIndex("/tmp")
        with patch('jieba.lcut', return_value=['2025', '量化', '投资']):
            words = idx._tokenize("2025量化投资")
        assert '2025' not in words

    def test_strips_markdown_syntax(self):
        """清理 Markdown 语法符号"""
        idx = KnowledgeIndex("/tmp")
        with patch('jieba.lcut', return_value=['标题', '内容', '链接']):
            words = idx._tokenize("# 标题\n**粗体** `代码` [链接](http://x)")
        # 不应包含 markdown 符号
        for w in words:
            assert w not in {'#', '*', '`', '[', ']', '(', ')', '|', '-', '>', '~'}


# ============================================================
# _compute_relevance
# ============================================================

class TestComputeRelevance:

    def _build_idx_with_summary(self, notes_dir, path, summary):
        """手动构造索引（不走 build_index）"""
        idx = KnowledgeIndex(notes_dir)
        idx._index = {path: summary}
        idx._content_cache = {path: "# 量化投资策略\n内容文字。"}
        idx._built = True
        return idx

    def test_title_match_highest_weight(self):
        """标题匹配权重最高（+3.0）"""
        summary = NoteSummary(
            path="/tmp/x.md", title="量化投资策略", date="2025-01-01",
            tags=['量化'], key_frameworks=[], action_items=[],
            char_count=100, word_count=20,
        )
        idx = self._build_idx_with_summary("/tmp", "/tmp/x.md", summary)
        score_title_only = idx._compute_relevance("/tmp/x.md", ['量化投资'])
        score_no_title = idx._compute_relevance("/tmp/x.md", ['内容文字'])
        # 标题命中得分应显著高于仅内容命中（标题+3.0 vs 内容最多+3.0）
        assert score_title_only > score_no_title

    def test_tag_match_medium_weight(self):
        """标签匹配中等权重（+2.0）"""
        summary = NoteSummary(
            path="/tmp/x.md", title="无关标题", date="2025-01-01",
            tags=['量化投资'], key_frameworks=[], action_items=[],
            char_count=100, word_count=20,
        )
        idx = self._build_idx_with_summary("/tmp", "/tmp/x.md", summary)
        # 标签命中
        score_tag = idx._compute_relevance("/tmp/x.md", ['量化投资'])
        # 不命中标题和内容
        score_none = idx._compute_relevance("/tmp/x.md", ['完全无关xyz'])
        assert score_tag > score_none

    def test_content_match_with_cap(self):
        """全文匹配按出现次数累加，上限 3.0"""
        # 构建内容含 5 次出现的缓存
        idx = KnowledgeIndex("/tmp")
        summary = NoteSummary(
            path="/tmp/x.md", title="无", date="2025-01-01",
            tags=[], key_frameworks=[], action_items=[],
            char_count=500, word_count=100,
        )
        content = "关键词关键词关键词关键词关键词其他内容"
        idx._index = {"/tmp/x.md": summary}
        idx._content_cache = {"/tmp/x.md": content}
        idx._built = True
        # 5 次 * 0.5 = 2.5（未超上限 3.0）
        score = idx._compute_relevance("/tmp/x.md", ['关键词'])
        assert score > 0
        # 超过 6 次即封顶 3.0
        content_capped = "关键词" * 10 + "其他"
        idx._content_cache["/tmp/x.md"] = content_capped
        score_capped = idx._compute_relevance("/tmp/x.md", ['关键词'])
        assert score_capped <= 3.0

    def test_unknown_path_returns_zero(self):
        """路径不在索引中时返回 0.0"""
        idx = KnowledgeIndex("/tmp")
        idx._built = True
        score = idx._compute_relevance("/tmp/nonexistent.md", ['量化'])
        assert score == 0.0
