"""
QualityGate 评分引擎单元测试

覆盖：
  - R0 内容完整性（短内容不通过）
  - 空笔记不通过
  - 报告包含 R7/R8/R9 规则

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_quality_gate.py -v
"""
import os
import pytest
from pathlib import Path

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
