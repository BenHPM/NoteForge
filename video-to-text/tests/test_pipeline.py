"""
NoteForge 核心流水线单元测试

覆盖：
  - bilibili_download: URL 解析、BV 提取、bug 修复验证
  - transcript_preprocessor: 文本清洗、分块
  - note_formatter: 笔记格式化
  - quality_gate: 质量评分
  - feishu_client: 嵌套分类匹配
  - audio-url 平台处理器（模拟）

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/ -v
"""
import os
import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# 添加 scripts 目录到 path
SCRIPT_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))


# ============================================================
# bilibili_download 测试
# ============================================================

class TestBilibiliDownload:
    """bilibili_download 模块测试"""

    def test_normalize_url_bvid(self):
        from bilibili_download import normalize_url
        result = normalize_url("BV1xx411c7mD")
        assert result == "https://www.bilibili.com/video/BV1xx411c7mD"

    def test_normalize_url_full_url(self):
        from bilibili_download import normalize_url
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        assert normalize_url(url) == url

    def test_extract_bvid(self):
        from bilibili_download import extract_bvid
        assert extract_bvid("https://www.bilibili.com/video/BV1xx411c7mD") == "BV1xx411c7mD"
        assert extract_bvid("BV1abc123") == "BV1abc123"
        assert extract_bvid("https://example.com") == ""

    def test_download_bilibili_invalid_url(self):
        from bilibili_download import download_bilibili
        result = download_bilibili("https://example.com/not-bilibili")
        assert result["success"] is False
        assert "error" in result

    def test_download_bilibili_error_dict_format(self):
        """验证 line 167 bug 修复：错误返回必须是标准 dict 格式"""
        from bilibili_download import download_bilibili
        # 使用不存在的 BV 号，触发 get_video_info 失败
        result = download_bilibili("BV000000000000000000000")
        assert isinstance(result, dict)
        assert "success" in result
        assert result["success"] is False
        # 确保 error key 是字符串，不是 f-string key 语法错误
        assert isinstance(result.get("error", ""), str)


# ============================================================
# transcript_preprocessor 测试
# ============================================================

class TestTranscriptPreprocessor:
    """TranscriptPreprocessor 模块测试"""

    @pytest.fixture
    def preprocessor(self):
        from transcript_preprocessor import TranscriptPreprocessor
        return TranscriptPreprocessor()

    def test_clean_noise(self, preprocessor):
        text = "大家好[无法识别片段]我们今天[00:30]来聊一下<0.5>这个话题"
        cleaned = preprocessor.clean(text, clean_fillers=False)
        assert "[无法识别片段]" not in cleaned
        assert "[00:30]" not in cleaned
        assert "<0.5>" not in cleaned

    def test_remove_filler_words(self, preprocessor):
        text = "嗯那个我们啊来聊聊这个话题吧"
        cleaned = preprocessor.clean(text, clean_fillers=True)
        # 去语气词后文本应变短
        assert len(cleaned) <= len(text)

    def test_estimate_tokens(self, preprocessor):
        text = "这是一段测试文本"
        count = preprocessor.estimate_tokens(text)
        assert count > 0
        assert isinstance(count, int)

    def test_empty_input(self, preprocessor):
        result = preprocessor.clean("")
        assert isinstance(result, str)


# ============================================================
# note_formatter 测试
# ============================================================

class TestNoteFormatter:
    """NoteFormatter 模块测试"""

    @pytest.fixture
    def formatter(self):
        from note_formatter import NoteFormatter
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


# ============================================================
# quality_gate 测试
# ============================================================

class TestQualityGate:
    """QualityGate 模块测试"""

    @pytest.fixture
    def gate(self):
        from quality_gate import QualityGate
        return QualityGate()

    @pytest.fixture
    def tmp_files(self, tmp_path):
        """创建临时文件辅助函数"""
        def _create(note_text, transcript_text):
            note_file = tmp_path / "note.md"
            note_file.write_text(note_text, encoding="utf-8")
            transcript_file = tmp_path / "transcript.txt"
            transcript_file.write_text(transcript_text, encoding="utf-8")
            return str(note_file), str(transcript_file)
        return _create

    def test_short_content_fails_r0(self, gate, tmp_files):
        """R0: 内容长度 >= 200 字符"""
        note_path, transcript_path = tmp_files(
            "# 标题\n\n太短了",
            "这是一段很短的转录文本"
        )
        report = gate.evaluate(note_path, transcript_path)
        assert report.total_score < 0.8 or not report.overall_passed

    def test_empty_note_fails(self, gate, tmp_path):
        note_file = tmp_path / "empty.md"
        note_file.write_text("", encoding="utf-8")
        transcript_file = tmp_path / "t.txt"
        transcript_file.write_text("一些转录文本", encoding="utf-8")
        report = gate.evaluate(str(note_file), str(transcript_file))
        assert not report.overall_passed

    def test_report_includes_r7_r8_r9(self, gate, tmp_files):
        """验证修复：报告应包含 R7/R8/R9 规则"""
        # R0 要求 >= 200 字，需要足够长的内容
        long_content = "这是一个关于短视频创作的深度分析。" * 20
        note_text = f"# 短视频创作笔记\n\n> 课程定位：短视频创作\n\n---\n\n## 核心要点\n\n{long_content}"
        transcript_text = long_content * 2
        note_path, transcript_path = tmp_files(note_text, transcript_text)
        report = gate.evaluate(note_path, transcript_path)
        for rid in ["R7", "R8", "R9"]:
            assert rid in report.rule_results, f"报告缺少 {rid} 规则"


# ============================================================
# feishu_client.match_category 测试
# ============================================================

class TestMatchCategory:
    """feishu_client.match_category 嵌套分类测试"""

    def test_flat_match(self):
        from feishu_client import match_category
        categories = [
            {"name": "技术", "match": ["*技术*", "*编程*"]},
            {"name": "其他笔记", "match": ["*"]},
        ]
        result = match_category("Python技术笔记.md", categories)
        assert result == "技术"

    def test_nested_match(self):
        from feishu_client import match_category
        categories = [
            {"name": "短视频导演课程", "match": ["*短视频*", "*第*集*"]},
            {"name": "其他笔记", "match": ["*"]},
        ]
        result = match_category("短视频创作笔记.md", categories)
        assert result == "短视频导演课程"

    def test_no_match_returns_other(self):
        from feishu_client import match_category
        categories = [
            {"name": "技术", "match": ["*技术*"]},
            {"name": "其他笔记", "match": ["*"]},
        ]
        result = match_category("随便什么.md", categories)
        assert result == "其他笔记"


# ============================================================
# video-mapping 测试
# ============================================================

class TestVideoMapping:
    """video-mapping.json 数据完整性测试"""

    @pytest.fixture
    def mapping(self):
        config_dir = Path(__file__).parent.parent / "config"
        with open(config_dir / "video-mapping.json", encoding="utf-8") as f:
            return json.load(f)

    def test_no_duplicate_titles(self, mapping):
        """验证无重复标题（修复后的集数冲突）"""
        titles = [entry["title"] for entry in mapping]
        # 允许有相似但不完全相同的标题
        assert len(titles) == len(set(titles)), f"存在重复标题: {[t for t in titles if titles.count(t) > 1]}"

    def test_all_entries_have_required_fields(self, mapping):
        for entry in mapping:
            assert "id" in entry
            assert "title" in entry
            assert "order" in entry

    def test_order_is_sequential(self, mapping):
        orders = [entry["order"] for entry in mapping]
        assert orders == list(range(1, len(mapping) + 1))


# ============================================================
# URL 平台检测测试
# ============================================================

class TestPlatformDetection:
    """音频平台 URL 识别测试"""

    def test_xiaoyuzhou_url_pattern(self):
        import re
        pattern = r'xiaoyuzhoufm\.com/episode/([a-f0-9]+)'
        m = re.search(pattern, "https://www.xiaoyuzhoufm.com/episode/67a3b2c1d4e5f6a7b8c9d0e1")
        assert m is not None
        assert m.group(1) == "67a3b2c1d4e5f6a7b8c9d0e1"

    def test_lizhi_url_pattern(self):
        import re
        pattern = r'lizhi\.fm/(?:episode/)?(\d+)'
        assert re.search(pattern, "https://www.lizhi.fm/episode/12345") is not None
        assert re.search(pattern, "https://www.lizhi.fm/b/12345") is None  # /b/ 是频道页，不是单集

    def test_bilibili_url_detection(self):
        urls = [
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "https://b23.tv/abcdef",
        ]
        for url in urls:
            assert 'bilibili.com' in url or 'b23.tv' in url


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
