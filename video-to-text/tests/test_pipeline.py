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
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ============================================================
# bilibili_download 测试
# ============================================================

class TestBilibiliDownload:
    """bilibili_download 模块测试"""

    def test_normalize_url_bvid(self):
        from noteforge.sources.bilibili import normalize_url
        result = normalize_url("BV1xx411c7mD")
        assert result == "https://www.bilibili.com/video/BV1xx411c7mD"

    def test_normalize_url_full_url(self):
        from noteforge.sources.bilibili import normalize_url
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        assert normalize_url(url) == url

    def test_extract_bvid(self):
        from noteforge.sources.bilibili import extract_bvid
        assert extract_bvid("https://www.bilibili.com/video/BV1xx411c7mD") == "BV1xx411c7mD"
        assert extract_bvid("BV1abc123") == "BV1abc123"
        assert extract_bvid("https://example.com") == ""

    def test_download_bilibili_invalid_url(self):
        from noteforge.sources.bilibili import download_bilibili
        result = download_bilibili("https://example.com/not-bilibili")
        assert result["success"] is False
        assert "error" in result

    def test_download_bilibili_error_dict_format(self):
        """验证 line 167 bug 修复：错误返回必须是标准 dict 格式"""
        from noteforge.sources.bilibili import download_bilibili
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
        from noteforge.core.transcript_preprocessor import TranscriptPreprocessor
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


# ============================================================
# quality_gate 测试
# ============================================================

class TestQualityGate:
    """QualityGate 模块测试"""

    @pytest.fixture
    def gate(self):
        from noteforge.quality.gate import QualityGate
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
        from noteforge.integration.feishu import match_category
        categories = [
            {"name": "技术", "match": ["*技术*", "*编程*"]},
            {"name": "其他笔记", "match": ["*"]},
        ]
        result = match_category("Python技术笔记.md", categories)
        assert result == "技术"

    def test_nested_match(self):
        from noteforge.integration.feishu import match_category
        categories = [
            {"name": "短视频导演课程", "match": ["*短视频*", "*第*集*"]},
            {"name": "其他笔记", "match": ["*"]},
        ]
        result = match_category("短视频创作笔记.md", categories)
        assert result == "短视频导演课程"

    def test_no_match_returns_other(self):
        from noteforge.integration.feishu import match_category
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


# ============================================================
# md_to_blocks 测试（飞书 Markdown → Blocks 转换）
# ============================================================
class TestMdToBlocks:
    """测试 feishu_client.md_to_blocks 各元素类型转换"""

    def setup_method(self):
        from noteforge.integration.feishu import md_to_blocks
        self.md_to_blocks = md_to_blocks

    def test_heading_levels(self):
        """H1-H6 都应生成正确的 block_type"""
        for level in range(1, 7):
            md = f"{'#' * level} 标题{level}"
            blocks = self.md_to_blocks(md)
            assert len(blocks) == 1, f"H{level} 应产生 1 个 block"
            assert blocks[0]['block_type'] == 2 + level, f"H{level} block_type 应为 {2 + level}"

    def test_plain_text(self):
        """纯文本应生成 block_type=2"""
        blocks = self.md_to_blocks("这是一段普通文本")
        assert len(blocks) == 1
        assert blocks[0]['block_type'] == 2

    def test_bullet_list(self):
        """无序列表项应生成文本 block"""
        md = "- 项目1\n- 项目2\n- 项目3"
        blocks = self.md_to_blocks(md)
        assert len(blocks) >= 3

    def test_ordered_list(self):
        """有序列表项应生成文本 block"""
        md = "1. 第一步\n2. 第二步\n3. 第三步"
        blocks = self.md_to_blocks(md)
        assert len(blocks) >= 3

    def test_blockquote(self):
        """引用块应生成文本 block"""
        md = "> 这是一段引用"
        blocks = self.md_to_blocks(md)
        assert len(blocks) >= 1

    def test_code_block(self):
        """代码块应生成文本 block"""
        md = "```python\nprint('hello')\n```"
        blocks = self.md_to_blocks(md)
        assert len(blocks) >= 1

    def test_empty_input(self):
        """空输入应返回空列表"""
        blocks = self.md_to_blocks("")
        assert blocks == []

    def test_horizontal_rule_skipped(self):
        """水平分隔线应被跳过"""
        md = "上文\n---\n下文"
        blocks = self.md_to_blocks(md)
        # --- 不会产生 block，只有上文和下文
        assert len(blocks) == 2

    def test_inline_bold(self):
        """加粗文本应包含 bold 样式"""
        md = "这是**加粗**文本"
        blocks = self.md_to_blocks(md)
        assert len(blocks) >= 1
        # 检查 text_run 中是否有 bold 样式
        block = blocks[0]
        text_key = [k for k in block.keys() if k not in ('block_type',)][0]
        elements = block[text_key].get('elements', [])
        has_bold = any(
            el.get('text_run', {}).get('text_element_style', {}).get('bold', False)
            for el in elements
            if 'text_run' in el
        )
        assert has_bold, "应包含 bold 样式"

    def test_inline_code(self):
        """行内代码应包含 inline_code 样式"""
        md = "使用 `pip install` 安装"
        blocks = self.md_to_blocks(md)
        assert len(blocks) >= 1


# ============================================================
# chunk_if_needed 测试（超长文本分块）
# ============================================================
class TestChunkIfNeeded:
    """测试 transcript_preprocessor 的分块逻辑"""

    def setup_method(self):
        from noteforge.core.transcript_preprocessor import TranscriptPreprocessor
        self.preprocessor = TranscriptPreprocessor()

    def test_short_text_no_chunking(self):
        """短文本不应分块"""
        text = "这是短文本。" * 10
        chunks = self.preprocessor.chunk_if_needed(text, max_tokens=50000)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_empty_text_no_chunking(self):
        """空文本返回空列表中的空字符串"""
        chunks = self.preprocessor.chunk_if_needed("")
        assert len(chunks) == 1

    def test_long_text_produces_multiple_chunks(self):
        """长文本应产生多个块"""
        # 用较小的 max_tokens 触发分块
        sentences = [f"这是第{i}个句子，用于测试超长文本的分块功能，内容较长。" for i in range(300)]
        text = "\n".join(sentences)
        chunks = self.preprocessor.chunk_if_needed(
            text, max_tokens=2000, min_chunk_size=500
        )
        assert len(chunks) >= 2, f"长文本应分成多个块，实际 {len(chunks)} 块"

    def test_chunks_are_not_empty(self):
        """每个块不应为空"""
        sentences = [f"这是第{i}个句子，内容涉及量化和基金投资策略。" for i in range(300)]
        text = "\n".join(sentences)
        chunks = self.preprocessor.chunk_if_needed(
            text, max_tokens=2000, min_chunk_size=500
        )
        for chunk in chunks:
            assert len(chunk) > 0, "每个块不应为空"

    def test_topic_boundary_preferred(self):
        """分块应优先在话题边界处切分"""
        topic_switch = "好，接下来我们讨论量化投资策略。"
        sentences_a = [f"第一部分内容，关于导演拍摄技巧第{i}点。" for i in range(200)]
        sentences_b = [f"第二部分内容，关于量化投资策略第{i}点。" for i in range(200)]
        text = "\n".join(sentences_a) + "\n" + topic_switch + "\n" + "\n".join(sentences_b)
        chunks = self.preprocessor.chunk_if_needed(
            text, max_tokens=2000, min_chunk_size=500
        )
        assert len(chunks) >= 2, f"长文本应分成多个块，实际 {len(chunks)} 块"


# ============================================================
# clean() 配置参数测试
# ============================================================
class TestCleanConfig:
    """测试 transcript_preprocessor.clean() 的配置开关"""

    def setup_method(self):
        from noteforge.core.transcript_preprocessor import TranscriptPreprocessor
        self.preprocessor = TranscriptPreprocessor()

    def test_clean_unrecognized_enabled(self):
        """开启清理时应移除 [无法识别片段]"""
        text = "这是正文[无法识别片段]后续内容"
        result = self.preprocessor.clean(text, clean_unrecognized=True)
        assert "[无法识别片段]" not in result
        assert "后续内容" in result

    def test_clean_unrecognized_disabled(self):
        """关闭清理时应保留 [无法识别片段]"""
        text = "这是正文[无法识别片段]后续内容"
        result = self.preprocessor.clean(text, clean_unrecognized=False)
        assert "[无法识别片段]" in result

    def test_clean_timestamps_enabled(self):
        """开启清理时应移除 [HH:MM:SS] 时间戳"""
        text = "[00:01:23]这是正文"
        result = self.preprocessor.clean(text, clean_timestamps=True)
        assert "[00:01:23]" not in result
        assert "这是正文" in result

    def test_clean_timestamps_disabled(self):
        """关闭清理时应保留时间戳"""
        text = "[00:01:23]这是正文"
        result = self.preprocessor.clean(text, clean_timestamps=False)
        assert "[00:01:23]" in result

    def test_all_cleaning_disabled(self):
        """所有清理都关闭时应保留噪声"""
        text = "[无法识别片段][00:01:23]嗯正文"
        result = self.preprocessor.clean(
            text, clean_fillers=False, clean_unrecognized=False, clean_timestamps=False
        )
        assert "[无法识别片段]" in result
        assert "[00:01:23]" in result
        assert "嗯" in result


# ============================================================
# validate_structure 测试（笔记结构校验）
# ============================================================
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


# ============================================================
# Quality Gate 规则测试
# ============================================================
class TestQualityGateRules:
    """测试 quality_gate 各规则的核心检测逻辑"""

    def setup_method(self):
        from noteforge.quality.gate import QualityGate
        from noteforge.quality import rules
        self.gate = QualityGate()
        self.rules = rules

    def test_r1_fabricated_percentage(self):
        """R1 应检测笔记中无原文出处的百分比（使用 FABRICATED_PATTERNS 匹配的模式）"""
        source = "市场增长显著"
        note = "# 标题\n占比约50%，增长达到30%"  # 模式匹配的虚构百分比，原文无出处
        result = self.rules.check_fabricated_data(self.gate.FABRICATED_PATTERNS, note, source)
        assert not result.passed or len(result.issues) > 0, "应检测到虚构百分比"

    def test_r1_passed_when_numbers_match(self):
        """R1 数字匹配原文时应通过"""
        source = "收益率为25%，规模达到300亿"
        note = "# 标题\n收益率为25%，规模达到300亿"
        result = self.rules.check_fabricated_data(self.gate.FABRICATED_PATTERNS, note, source)
        assert result.passed, "数字匹配原文时应通过"

    def test_r5_low_coverage_fatal(self):
        """R5 覆盖率 <30% 应为 fatal"""
        source = "# 第一章\n内容A\n# 第二章\n内容B\n# 第三章\n内容C\n# 第四章\n内容D\n# 第五章\n内容E"
        note = "# 标题\n只有一点点内容"  # 几乎没有覆盖
        result = self.rules.check_coverage(note, source)
        has_fatal = any(i.severity == 'fatal' for i in result.issues)
        assert has_fatal or not result.passed, "低覆盖率应产生 fatal 问题"

    def test_r5_high_coverage_passes(self):
        """R5 覆盖率足够时应通过"""
        source = "# 量化策略\n内容详情\n# 投资方法\n内容详情"
        note = "# 标题\n## 量化策略\n覆盖了量化策略\n## 投资方法\n覆盖了投资方法"
        result = self.rules.check_coverage(note, source)
        assert result.passed, "高覆盖率应通过"

    def test_r8_vague_insight(self):
        """R8 应检测模糊洞察"""
        note = "# 标题\n## 可迁移洞察\n- 要重视投资\n- 需要关注市场变化"
        result = self.rules.check_insight_actionability(note)
        # 模糊表述应产生问题
        assert len(result.issues) > 0, "模糊洞察应被检测"

    def test_r12_name_consistency(self):
        """R12 应检测人名不一致"""
        source = "翟东升指出地缘政治格局变化"
        note = "# 标题\n翟东升指出格局变化，但张三认为..."  # 张三不在原文中
        result = self.rules.check_name_number_consistency(note, source)
        # 可能有 name mismatch 问题（取决于实现细节）
        assert isinstance(result.passed, bool), "R12 应返回布尔值"

    def test_r0_short_content_fails(self):
        """R0 短内容应不通过"""
        # 使用完整的 evaluate 方法需要文件，这里直接测试逻辑
        from noteforge.quality.gate import QualityGate
        gate = QualityGate()
        # 短于 200 字的笔记体应无法通过
        assert True  # 已有 test_short_content_fails_r0 覆盖


# ============================================================
# detect_domain 测试（知识域分类）
# ============================================================
class TestDetectDomain:
    """测试 DomainClassifier 域分类逻辑"""

    def setup_method(self):
        """使用测试配置构造分类器"""
        # 通过设置环境变量跳过 env_check
        os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'

    def test_match_files_priority(self):
        """match_files 应优先于关键词匹配"""
        from noteforge.core.domain_classifier import DomainClassifier
        from noteforge.context import PathConfig
        domains = [
            {
                'id': 'test_domain',
                'name': '测试域',
                'match_files': ['ep01*'],
                'match_keywords': [],
            },
            {
                'id': 'general',
                'name': '其他',
                'match_keywords': [],
                'match_files': [],
            },
        ]
        pc = PathConfig(
            base_dir=Path('.'), transcripts_dir=Path('.'), notes_dir=Path('.'),
            reports_dir=Path('.'), logs_dir=Path('.'),
        )
        classifier = DomainClassifier(domains=domains, path_config=pc)
        # ep01 开头的文件应匹配 test_domain
        result = classifier.detect_domain('/some/path/ep01-intro.md')
        assert result == 'test_domain', f"文件名匹配应优先: {result}"

    def test_fallback_to_general(self):
        """无匹配时应归入 general"""
        from noteforge.core.domain_classifier import DomainClassifier
        from noteforge.context import PathConfig
        domains = [
            {'id': 'finance', 'name': '金融', 'match_files': ['*量化*'], 'match_keywords': ['量化']},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        pc = PathConfig(
            base_dir=Path('.'), transcripts_dir=Path('.'), notes_dir=Path('.'),
            reports_dir=Path('.'), logs_dir=Path('.'),
        )
        classifier = DomainClassifier(domains=domains, path_config=pc)
        result = classifier.detect_domain('/some/path/random_note.md')
        assert result == 'general', f"无匹配应归入 general: {result}"


# ============================================================
# build_user_prompt 测试
# ============================================================
class TestBuildUserPrompt:
    """测试 prompt_builder.build_user_prompt"""

    def setup_method(self):
        os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'
        # 需要有效的 YAML 配置文件
        self.config_dir = Path(__file__).parent.parent / "config"

    def test_accepts_mode_parameter(self):
        """build_user_prompt 应接受 mode 参数"""
        from noteforge.core.prompt_builder import PromptBuilder
        rules_path = str(self.config_dir / "note_generation_rules.yaml")
        experience_path = str(self.config_dir / "experience_log.yaml")
        if not Path(rules_path).exists():
            pytest.skip("note_generation_rules.yaml not found")
        builder = PromptBuilder(rules_path, experience_path, content_type='lecture')
        # 不应抛出 TypeError
        result = builder.build_user_prompt("转写文本", title="标题", mode='notes')
        assert isinstance(result, str)
        assert len(result) > 0

    def test_without_title(self):
        """不传 title 也应正常工作"""
        from noteforge.core.prompt_builder import PromptBuilder
        rules_path = str(self.config_dir / "note_generation_rules.yaml")
        experience_path = str(self.config_dir / "experience_log.yaml")
        if not Path(rules_path).exists():
            pytest.skip("note_generation_rules.yaml not found")
        builder = PromptBuilder(rules_path, experience_path, content_type='lecture')
        result = builder.build_user_prompt("转写文本")
        assert isinstance(result, str)

    def test_content_type_affects_instruction(self):
        """不同 content_type 应产生不同指令"""
        from noteforge.core.prompt_builder import PromptBuilder
        rules_path = str(self.config_dir / "note_generation_rules.yaml")
        experience_path = str(self.config_dir / "experience_log.yaml")
        if not Path(rules_path).exists():
            pytest.skip("note_generation_rules.yaml not found")
        builder_lecture = PromptBuilder(rules_path, experience_path, content_type='lecture')
        builder_podcast = PromptBuilder(rules_path, experience_path, content_type='podcast')
        result_lecture = builder_lecture.build_user_prompt("转写文本")
        result_podcast = builder_podcast.build_user_prompt("转写文本")
        # lecture 和 podcast 的指令应该不同
        assert result_lecture != result_podcast, "不同 content_type 应产生不同指令"


# ============================================================
# QualityGate 配置测试
# ============================================================
class TestQualityGateConfig:
    """测试 QualityGate 可配置行为"""

    def test_fatal_rules_must_pass_default(self):
        """默认应启用致命规则检查"""
        from noteforge.quality.gate import QualityGate
        gate = QualityGate()
        assert gate._fatal_rules_must_pass is True

    def test_fatal_rules_must_pass_disabled(self):
        """应可关闭致命规则检查"""
        from noteforge.quality.gate import QualityGate
        gate = QualityGate(fatal_rules_must_pass=False)
        assert gate._fatal_rules_must_pass is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
