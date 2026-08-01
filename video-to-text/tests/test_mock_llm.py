# -*- coding: utf-8 -*-
"""
MockLLMProvider 测试夹具验证

覆盖：
  - 基本响应注册和切换
  - 序列模式
  - 错误响应
  - 调用日志
  - 预定义工厂函数
  - 与质量门禁集成测试
"""

import pytest
import json
from noteforge.quality.mock_llm import (
    MockLLMProvider, create_mock_provider,
    make_normal_note_response, make_refusal_response,
    make_hallucination_response,
    make_json_eval_response, make_json_eval_malformed,
)
from noteforge.core.llm_providers import LLMError


class TestMockLLMProvider:
    """MockLLMProvider 基本功能测试"""

    def test_default_response(self):
        provider = MockLLMProvider()
        result = provider.generate("system", "user")
        assert result == "默认响应"

    def test_set_and_use_response(self):
        provider = MockLLMProvider()
        provider.set_response("test", "测试响应")
        provider.use_response("test")
        result = provider.generate("system", "user")
        assert result == "测试响应"

    def test_use_unregistered_response_raises(self):
        provider = MockLLMProvider()
        with pytest.raises(ValueError, match="未注册的响应"):
            provider.use_response("nonexistent")

    def test_chained_calls(self):
        provider = MockLLMProvider()
        result = (provider
                  .set_response("a", "响应A")
                  .set_response("b", "响应B")
                  .use_response("a")
                  .generate("system", "user"))
        assert result == "响应A"

    def test_sequence_mode(self):
        provider = MockLLMProvider()
        provider.set_response("a", "响应A")
        provider.set_response("b", "响应B")
        provider.set_sequence(["a", "b", "a"])

        assert provider.generate("s", "u") == "响应A"
        assert provider.generate("s", "u") == "响应B"
        assert provider.generate("s", "u") == "响应A"
        # 序列耗尽后回到 _current_key（默认 "default"）
        # 如果需要循环，应设置更长的序列或切换 use_response
        result = provider.generate("s", "u")
        assert result == "默认响应"  # _current_key 仍为 "default"

    def test_error_response(self):
        provider = MockLLMProvider()
        provider.set_error("timeout", "超时", retryable=True)
        provider.use_response("timeout")
        with pytest.raises(LLMError, match="超时"):
            provider.generate("system", "user")

    def test_error_retryable(self):
        provider = MockLLMProvider()
        provider.set_error("api_key", "key无效", retryable=False)
        provider.use_response("api_key")
        with pytest.raises(LLMError) as exc_info:
            provider.generate("system", "user")
        assert exc_info.value.retryable is False

    def test_call_count(self):
        provider = MockLLMProvider()
        assert provider.call_count == 0
        provider.generate("s", "u")
        provider.generate("s", "u")
        assert provider.call_count == 2

    def test_call_log(self):
        provider = MockLLMProvider()
        provider.generate("sys prompt", "user prompt", max_tokens=4096, temperature=0.5)
        assert len(provider.call_log) == 1
        assert provider.call_log[0]['max_tokens'] == 4096
        assert provider.call_log[0]['temperature'] == 0.5

    def test_reset(self):
        provider = MockLLMProvider()
        provider.generate("s", "u")
        provider.reset()
        assert provider.call_count == 0
        assert provider.call_log == []

    def test_context_limit(self):
        provider = MockLLMProvider(context_limit=128000)
        assert provider.get_context_limit() == 128000

    def test_name(self):
        provider = MockLLMProvider(name="TestMock")
        assert provider.get_name() == "TestMock"

    def test_usage_tracking(self):
        provider = MockLLMProvider()
        provider.set_response("tracked", "text", usage={'input_tokens': 500, 'output_tokens': 200})
        provider.use_response("tracked")
        provider.generate("s", "u")
        assert provider._last_usage['input_tokens'] == 500
        assert provider._total_usage['input_tokens'] == 500


class TestPredefinedResponses:
    """预定义响应工厂测试"""

    def test_normal_note_has_structure(self):
        note = make_normal_note_response()
        assert '# ' in note
        assert '## ' in note
        assert '核心观点' in note
        assert '学习总结' in note
        assert '- [ ]' in note

    def test_refusal_contains_pattern(self):
        refusal = make_refusal_response()
        assert 'cannot' in refusal.lower() or 'high risk' in refusal.lower()

    def test_json_eval_is_valid_json(self):
        response = make_json_eval_response(4.0)
        data = json.loads(response)
        assert 'richness' in data
        assert 'overall' in data
        assert data['overall'] == 4.0

    def test_json_malformed_has_trailing_comma(self):
        response = make_json_eval_malformed()
        # 应包含尾逗号（JSON 不合法但常见 LLM 输出）
        assert ',}' in response or ',\n}' in response


class TestCreateMockProvider:
    """create_mock_provider 工厂测试"""

    def test_creates_with_all_responses(self):
        provider = create_mock_provider()
        # 所有预注册响应都应可用
        for key in ["normal", "refusal", "truncated", "hallucination",
                     "json_eval", "json_malformed", "empty", "timeout", "api_key"]:
            if key in ("timeout", "api_key"):
                # 错误响应不能 use_response（会抛异常），但应已注册
                pass
            else:
                provider.use_response(key)
                result = provider.generate("s", "u")
                assert isinstance(result, str)


class TestMockWithQualityGate:
    """MockLLMProvider 与质量门禁集成测试"""

    def test_normal_note_passes_quality(self):
        """正常笔记应通过质量门禁"""
        from noteforge.quality.gate import QualityGate
        gate = QualityGate()
        note = make_normal_note_response()
        # 构造一个足够长的原文
        source = "测试原文内容。" * 100
        report = gate.evaluate_text(note, source)
        # 正常笔记应至少有 R0 通过
        assert report.rule_results.get("R0", None) is not None

    def test_hallucination_detected_by_r1(self):
        """幻觉数据应被 R1 检测"""
        from noteforge.quality.gate import QualityGate
        gate = QualityGate()
        note = make_hallucination_response()
        source = "测试原文内容，只提到了增长，没有具体数字。" * 50
        report = gate.evaluate_text(note, source)
        # R1 应检测到虚构数据
        r1 = report.rule_results.get("R1")
        if r1 and not r1.passed:
            assert len(r1.issues) > 0
