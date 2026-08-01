# -*- coding: utf-8 -*-
"""
Provider Schema 契约测试 — 验证 LLM API 响应格式解析

P1-3: 确保 _parse_200 能正确处理 API 响应的各种格式，
包括标准格式、边界情况和未来可能的变更。

这些测试不调用真实 API，使用模拟的 HTTP 响应对象。
"""

import pytest
import json
from unittest.mock import MagicMock

from noteforge.core.llm_providers import (
    ClaudeProvider, OpenAIProvider, LLMProvider, LLMError, _RetryRequest,
)


def _make_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """创建模拟的 HTTP 响应"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


class TestClaudeProviderContract:
    """Claude API 响应契约测试"""

    def setup_method(self):
        """创建 ClaudeProvider（不需要有效 API key，只测解析逻辑）"""
        # 使用代理模式跳过 API key 检查
        self.provider = ClaudeProvider.__new__(ClaudeProvider)
        self.provider.__init__({
            'model': 'claude-sonnet-4-20250514',
            'base_url': 'http://127.0.0.1:15721',
            'api_key': 'PROXY_MANAGED',
            'api_retry': {'max_attempts': 1},
        })

    def test_standard_text_response(self):
        """标准 text 响应应正确解析"""
        resp = _make_response({
            'content': [{'type': 'text', 'text': '正常笔记内容'}],
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 100, 'output_tokens': 50},
        })
        result = self.provider._parse_200(resp, 'http://test')
        assert result == '正常笔记内容'
        assert self.provider._last_stop_reason == 'end_turn'

    def test_thinking_plus_text_response(self):
        """thinking + text 双块响应应取 text 块"""
        resp = _make_response({
            'content': [
                {'type': 'thinking', 'thinking': '推理过程...'},
                {'type': 'text', 'text': '最终答案'},
            ],
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 100, 'output_tokens': 50,
                      'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 50},
        })
        result = self.provider._parse_200(resp, 'http://test')
        assert result == '最终答案'

    def test_only_thinking_block(self):
        """只有 thinking 块时应回退取 thinking 内容"""
        resp = _make_response({
            'content': [
                {'type': 'thinking', 'thinking': '推理过程'},
            ],
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 100, 'output_tokens': 50},
        })
        result = self.provider._parse_200(resp, 'http://test')
        assert result == '推理过程'

    def test_empty_content_raises(self):
        """空 content 列表应抛出 LLMError"""
        resp = _make_response({
            'content': [],
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 100, 'output_tokens': 0},
        })
        with pytest.raises(LLMError):
            self.provider._parse_200(resp, 'http://test')

    def test_text_too_short_raises(self):
        """过短文本应被内容过滤检测"""
        resp = _make_response({
            'content': [{'type': 'text', 'text': ''}],
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 100, 'output_tokens': 1},
        })
        with pytest.raises(LLMError, match="空内容|安全过滤"):
            self.provider._parse_200(resp, 'http://test')

    def test_max_tokens_stop_reason_ok(self):
        """max_tokens stop_reason 不应触发过滤"""
        resp = _make_response({
            'content': [{'type': 'text', 'text': '这是一段很长的笔记内容，包含多个段落和详细的分析。'}],
            'stop_reason': 'max_tokens',
            'usage': {'input_tokens': 100, 'output_tokens': 8192},
        })
        result = self.provider._parse_200(resp, 'http://test')
        assert '笔记内容' in result

    def test_stop_sequence_stop_reason_ok(self):
        """stop_sequence stop_reason 不应触发过滤"""
        resp = _make_response({
            'content': [{'type': 'text', 'text': '这是一段正常的笔记内容，结构完整。'}],
            'stop_reason': 'stop_sequence',
            'usage': {'input_tokens': 100, 'output_tokens': 50},
        })
        result = self.provider._parse_200(resp, 'http://test')
        assert '笔记内容' in result

    def test_api_version_pinned(self):
        """API 版本应固定"""
        assert self.provider._api_version == '2023-06-01'

    def test_usage_tracking(self):
        """token 使用量应正确追踪"""
        resp = _make_response({
            'content': [{'type': 'text', 'text': '这是一段正常的内容，足够长以通过过滤。'}],
            'stop_reason': 'end_turn',
            'usage': {
                'input_tokens': 500,
                'output_tokens': 200,
                'cache_creation_input_tokens': 100,
                'cache_read_input_tokens': 50,
            },
        })
        self.provider._parse_200(resp, 'http://test')
        assert self.provider._last_usage['input_tokens'] == 500
        assert self.provider._last_usage['output_tokens'] == 200
        assert self.provider._last_cache_creation == 100
        assert self.provider._last_cache_read == 50


class TestOpenAIProviderContract:
    """OpenAI API 响应契约测试"""

    def setup_method(self):
        self.provider = OpenAIProvider.__new__(OpenAIProvider)
        # 直接调用父类 __init__ 和自身 __init__
        LLMProvider.__init__(self.provider)
        self.provider.model = 'gpt-4o'
        self.provider.base_url = 'https://api.openai.com/v1'
        self.provider.max_tokens = 8192
        self.provider.temperature = 0.3
        self.provider.api_key = 'test-key'

    def test_standard_response(self):
        """标准 OpenAI 响应应正确解析"""
        resp = _make_response({
            'choices': [{
                'message': {'content': '这是一段正常笔记内容，包含多个段落和详细的分析，长度足够通过内容过滤检测。'},
                'finish_reason': 'stop',
            }],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50},
        })
        result = self.provider._parse_200(resp, 'http://test')
        assert '正常笔记内容' in result
        assert self.provider._last_stop_reason == 'stop'

    def test_content_filter_finish_reason(self):
        """content_filter finish_reason 应触发重试"""
        self.provider._filter_hits = 0  # 重置
        resp = _make_response({
            'choices': [{
                'message': {'content': ''},
                'finish_reason': 'content_filter',
            }],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 0},
        })
        with pytest.raises(_RetryRequest):
            self.provider._parse_200(resp, 'http://test')

    def test_length_finish_reason_ok(self):
        """length finish_reason 应正常返回"""
        resp = _make_response({
            'choices': [{
                'message': {'content': '这是一段正常的内容，虽然被截断但仍有价值。'},
                'finish_reason': 'length',
            }],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 4096},
        })
        result = self.provider._parse_200(resp, 'http://test')
        assert '正常的内容' in result

    def test_empty_choices_raises(self):
        """空 choices 应抛出 LLMError"""
        resp = _make_response({
            'choices': [],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 0},
        })
        with pytest.raises(LLMError, match="空内容"):
            self.provider._parse_200(resp, 'http://test')
