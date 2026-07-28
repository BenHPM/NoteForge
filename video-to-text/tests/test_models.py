# -*- coding: utf-8 -*-
"""GenerationResult 数据模型单元测试（7 tests）。"""
import os
import pytest

class TestGenerationResult:
    """GenerationResult 数据模型测试"""

    def test_default_values(self):
        """默认值应正确"""
        from noteforge.models import GenerationResult
        r = GenerationResult(transcript_path="/path/to/transcript.txt")
        assert r.transcript_path == "/path/to/transcript.txt"
        assert r.note_path == ""
        assert r.quality_report_path == ""
        assert r.total_score == 0.0
        assert r.overall_passed is False
        assert r.attempts == 0
        assert r.duration_seconds == 0.0
        assert r.token_usage == {}
        assert r.error is None

    def test_to_dict_returns_dict(self):
        """to_dict 应返回标准字典"""
        from noteforge.models import GenerationResult
        r = GenerationResult(
            transcript_path="/path/transcript.txt",
            note_path="/path/note.md",
            total_score=0.85,
            overall_passed=True,
        )
        d = r.to_dict()
        assert isinstance(d, dict)
        assert d['transcript_path'] == "/path/transcript.txt"
        assert d['note_path'] == "/path/note.md"
        assert d['total_score'] == 0.85
        assert d['overall_passed'] is True

    def test_to_dict_includes_all_fields(self):
        """to_dict 应包含所有字段"""
        from noteforge.models import GenerationResult
        r = GenerationResult(transcript_path="t.txt")
        d = r.to_dict()
        expected_keys = {
            'transcript_path', 'note_path', 'quality_report_path',
            'total_score', 'overall_passed', 'attempts',
            'duration_seconds', 'token_usage', 'error',
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_with_all_values(self):
        """to_dict 应正确反映所有赋值"""
        from noteforge.models import GenerationResult
        r = GenerationResult(
            transcript_path="t.txt",
            note_path="n.md",
            quality_report_path="q.json",
            total_score=0.92,
            overall_passed=True,
            attempts=2,
            duration_seconds=45.3,
            token_usage={"input_tokens": 1000, "output_tokens": 500},
            error=None,
        )
        d = r.to_dict()
        assert d['attempts'] == 2
        assert d['duration_seconds'] == 45.3
        assert d['token_usage']['input_tokens'] == 1000
        assert d['error'] is None

    def test_token_usage_independent_between_instances(self):
        """不同实例的 token_usage 应相互独立"""
        from noteforge.models import GenerationResult
        r1 = GenerationResult(transcript_path="a.txt")
        r2 = GenerationResult(transcript_path="b.txt")
        r1.token_usage['input'] = 100
        assert 'input' not in r2.token_usage

    def test_error_field(self):
        """error 字段应正确赋值"""
        from noteforge.models import GenerationResult
        r = GenerationResult(transcript_path="t.txt", error="已存在（跳过）")
        assert r.error == "已存在（跳过）"

    def test_default_error_is_none(self):
        """默认 error 应为 None"""
        from noteforge.models import GenerationResult
        r = GenerationResult(transcript_path="t.txt")
        assert r.error is None
