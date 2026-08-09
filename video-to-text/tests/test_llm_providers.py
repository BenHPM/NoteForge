"""
NoteForge LLM 提供商抽象层单元测试

覆盖：
  - create_provider: 工厂函数创建各类型 provider
  - ClaudeProvider: 配置解析、代理模式、名称/上下文限制
  - OpenAIProvider: 配置解析、名称/上下文限制
  - LLMError: 异常属性
  - LLMProvider 基类: 内容过滤检测、使用量追踪

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_llm_providers.py -v
"""
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# 跳过环境检查

class TestCreateProvider:
    """create_provider 工厂函数测试"""

    def test_claude_provider_creation(self):
        """create_provider 应创建 ClaudeProvider"""
        from noteforge.core.llm_providers import create_provider, ClaudeProvider
        config = {'type': 'claude', 'claude': {'model': 'test-model', 'api_key_env': 'ANTHROPIC_API_KEY'}}
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test-key'}):
            provider = create_provider(config)
        assert isinstance(provider, ClaudeProvider)

    def test_openai_provider_creation(self):
        """create_provider 应创建 OpenAIProvider"""
        from noteforge.core.llm_providers import create_provider, OpenAIProvider
        config = {'type': 'openai', 'openai': {'model': 'gpt-4', 'api_key_env': 'OPENAI_API_KEY'}}
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-test-key'}):
            provider = create_provider(config)
        assert isinstance(provider, OpenAIProvider)

    def test_local_provider_creation(self):
        """create_provider 应创建 LocalProvider"""
        from noteforge.core.llm_providers import create_provider, LocalProvider
        config = {'type': 'local', 'local': {'model': 'qwen2.5-72b'}}
        provider = create_provider(config)
        assert isinstance(provider, LocalProvider)

    def test_invalid_provider_raises(self):
        """不支持的 provider 类型应抛出 LLMError"""
        from noteforge.core.llm_providers import create_provider, LLMError
        with pytest.raises(LLMError, match="不支持的 LLM 提供商"):
            create_provider({'type': 'nonexistent'})

    def test_default_type_is_claude(self):
        """未指定 type 时默认为 claude"""
        from noteforge.core.llm_providers import create_provider, ClaudeProvider
        config = {'claude': {'model': 'test-model', 'api_key_env': 'ANTHROPIC_API_KEY'}}
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test-key'}):
            provider = create_provider(config)
        assert isinstance(provider, ClaudeProvider)

    def test_api_retry_config_passed(self):
        """api_retry 配置应传递给 provider"""
        from noteforge.core.llm_providers import create_provider
        config = {
            'type': 'claude',
            'claude': {'model': 'test-model', 'api_key': 'test-key'},
            'api_retry': {'max_attempts': 5, 'base_delay': 20},
        }
        provider = create_provider(config)
        assert provider.max_retries == 5
        assert provider.base_delay == 20


class TestProviderConfig:
    """Provider 配置解析测试"""

    def test_proxy_managed_key(self):
        """PROXY_MANAGED api_key should not use env var"""
        from noteforge.core.llm_providers import ClaudeProvider
        config = {'model': 'test', 'api_key': 'PROXY_MANAGED', 'base_url': 'http://localhost:15721'}
        provider = ClaudeProvider(config)
        assert provider._using_direct_api is False

    def test_get_name(self):
        """get_name 应返回包含 'claude' 的名称"""
        from noteforge.core.llm_providers import ClaudeProvider
        config = {'model': 'claude-3', 'api_key': 'test-key'}
        provider = ClaudeProvider(config)
        assert 'claude' in provider.get_name().lower()

    def test_get_context_limit(self):
        """ClaudeProvider 上下文限制应为正数"""
        from noteforge.core.llm_providers import ClaudeProvider
        config = {'model': 'claude-3', 'api_key': 'test-key'}
        provider = ClaudeProvider(config)
        assert provider.get_context_limit() > 0

    def test_claude_context_limit_200k(self):
        """ClaudeProvider 上下文限制应为 200000"""
        from noteforge.core.llm_providers import ClaudeProvider
        config = {'model': 'claude-3', 'api_key': 'test-key'}
        provider = ClaudeProvider(config)
        assert provider.get_context_limit() == 200000

    def test_openai_context_limit_128k(self):
        """OpenAIProvider 上下文限制应为 128000"""
        from noteforge.core.llm_providers import OpenAIProvider
        config = {'model': 'gpt-4', 'api_key': 'test-key'}
        provider = OpenAIProvider(config)
        assert provider.get_context_limit() == 128000

    def test_openai_get_name(self):
        """OpenAIProvider 名称应包含 'openai'"""
        from noteforge.core.llm_providers import OpenAIProvider
        config = {'model': 'gpt-4', 'api_key': 'test-key'}
        provider = OpenAIProvider(config)
        assert 'openai' in provider.get_name().lower()

    def test_local_context_limit(self):
        """LocalProvider 上下文限制应为 32000"""
        from noteforge.core.llm_providers import LocalProvider
        config = {'model': 'qwen2.5-72b'}
        provider = LocalProvider(config)
        assert provider.get_context_limit() == 32000

    def test_local_get_name(self):
        """LocalProvider 名称应包含 'local'"""
        from noteforge.core.llm_providers import LocalProvider
        config = {'model': 'qwen2.5-72b'}
        provider = LocalProvider(config)
        assert 'local' in provider.get_name().lower()

    def test_claude_missing_api_key_raises(self):
        """ClaudeProvider 缺少 API key 应抛出 LLMError"""
        from noteforge.core.llm_providers import ClaudeProvider, LLMError
        config = {'model': 'claude-3', 'api_key': ''}
        with patch.dict(os.environ, {}, clear=False):
            # 确保环境变量不存在
            os.environ.pop('ANTHROPIC_API_KEY', None)
            with pytest.raises(LLMError, match="未设置"):
                ClaudeProvider(config)

    def test_openai_missing_api_key_raises(self):
        """OpenAIProvider 缺少 API key 应抛出 LLMError"""
        from noteforge.core.llm_providers import OpenAIProvider, LLMError
        config = {'model': 'gpt-4', 'api_key': ''}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('OPENAI_API_KEY', None)
            with pytest.raises(LLMError, match="未设置"):
                OpenAIProvider(config)

    def test_claude_proxy_auto_managed(self):
        """代理模式下无 API key 应自动设置为 PROXY_MANAGED"""
        from noteforge.core.llm_providers import ClaudeProvider
        config = {
            'model': 'test',
            'base_url': 'http://localhost:15721',
            'api_key': '',
        }
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('ANTHROPIC_API_KEY', None)
            provider = ClaudeProvider(config)
        assert provider.api_key == 'PROXY_MANAGED'
        assert provider._using_direct_api is False

    def test_claude_placeholder_ignored(self):
        """PLACEHOLDER api_key 应被忽略，回退到环境变量"""
        from noteforge.core.llm_providers import ClaudeProvider
        config = {'model': 'test', 'api_key': 'PLACEHOLDER'}
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-from-env'}):
            provider = ClaudeProvider(config)
        assert provider.api_key == 'sk-from-env'

    def test_claude_direct_api_key(self):
        """直接配置的 api_key 应优先使用"""
        from noteforge.core.llm_providers import ClaudeProvider
        config = {'model': 'test', 'api_key': 'sk-direct-key'}
        provider = ClaudeProvider(config)
        assert provider.api_key == 'sk-direct-key'
        assert provider._using_direct_api is True

    def test_default_model_names(self):
        """未指定 model 时应使用默认模型名"""
        from noteforge.core.llm_providers import ClaudeProvider, OpenAIProvider, LocalProvider
        assert ClaudeProvider({'api_key': 'test'}).model == 'claude-sonnet-4-20250514'
        assert OpenAIProvider({'api_key': 'test'}).model == 'gpt-4o'
        assert LocalProvider({}).model == 'qwen2.5-72b'


class TestLLMError:
    """LLMError 异常测试"""

    def test_error_attributes(self):
        """LLMError 应正确设置属性"""
        from noteforge.core.llm_providers import LLMError
        err = LLMError("test error", status_code=429, retryable=True)
        assert str(err) == "test error"
        assert err.status_code == 429
        assert err.retryable is True

    def test_default_attributes(self):
        """LLMError 默认属性应为 0 和 False"""
        from noteforge.core.llm_providers import LLMError
        err = LLMError("test error")
        assert err.status_code == 0
        assert err.retryable is False


class TestContentFilter:
    """内容安全过滤检测测试"""

    def test_empty_text_filtered(self):
        """空文本应被过滤"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider({'api_key': 'test'})
        assert provider._is_content_filtered('') is True

    def test_whitespace_only_filtered(self):
        """纯空格文本应被过滤"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider({'api_key': 'test'})
        assert provider._is_content_filtered('   ') is True

    def test_short_text_filtered(self):
        """<20 字符的短文本应被过滤"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider({'api_key': 'test'})
        assert provider._is_content_filtered('短文本') is True

    def test_medium_text_with_filter_pattern(self):
        """20-200 字符包含过滤关键词应被过滤"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider({'api_key': 'test'})
        text = "I cannot provide this information due to policy."
        assert provider._is_content_filtered(text) is True

    def test_medium_text_without_filter_pattern(self):
        """20-200 字符不包含过滤关键词应通过"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider({'api_key': 'test'})
        text = "这是一段正常的知识笔记内容，包含多个要点和分析。"
        assert provider._is_content_filtered(text) is False

    def test_long_text_not_filtered(self):
        """>=200 字符的长文本应通过"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider({'api_key': 'test'})
        text = "A" * 200
        assert provider._is_content_filtered(text) is False

    def test_adaptive_threshold_high_false_positive(self):
        """高误判率时应放宽过滤阈值（<10 字符才过滤）"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider({'api_key': 'test'})
        provider._filter_hits = 10
        provider._filter_false_pos = 8  # 80% 误判率
        # 高误判率下阈值放宽到 <10 字符
        # 18 字符的文本，高误判率下不会被过滤（>=10 字符通过）
        text_medium = "这是一段三十字左右的知识笔记内容整理"
        assert provider._is_content_filtered(text_medium) is False
        # 5 字符的文本，高误判率下仍会被过滤（<10 字符）
        text_short = "短文本"
        assert provider._is_content_filtered(text_short) is True

    def test_adaptive_threshold_medium_false_positive(self):
        """中等误判率时应放宽过滤阈值到 <30 字符"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider({'api_key': 'test'})
        provider._filter_hits = 10
        provider._filter_false_pos = 3  # 30% 误判率
        # 25 字符的文本，中等误判率下会被过滤（<30 字符）
        text_25 = "这是一段约二十五字的知识笔记"  # ~15 chars
        assert provider._is_content_filtered(text_25) is True


class TestUsageTracking:
    """Token 使用量追踪测试"""

    def test_initial_usage_zero(self):
        """初始使用量应为零"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider({'api_key': 'test'})
        usage = provider.get_usage()
        assert usage['input_tokens'] == 0
        assert usage['output_tokens'] == 0

    def test_initial_total_usage_zero(self):
        """初始累计使用量应为零"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider({'api_key': 'test'})
        total = provider.get_total_usage()
        assert total['input_tokens'] == 0
        assert total['output_tokens'] == 0
        assert total['calls'] == 0

    def test_usage_returns_copy(self):
        """get_usage 应返回副本，不影响内部状态"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider({'api_key': 'test'})
        usage = provider.get_usage()
        usage['input_tokens'] = 999
        assert provider.get_usage()['input_tokens'] == 0


# ============================================================
# P3: Prompt Caching 真实有效性检测
# 背景：请求经 cc-switch 代理路由到非 Anthropic 模型（如 deepseek-v4-flash）
# 时，后端不识别 Anthropic cache_control，只做自动前缀缓存。
# 通过观测后端是否返回 cache_creation/cache_read 字段来判断真实命中，
# 并在多轮调用后仍无缓存字段时发出一次警告。
# ============================================================

class TestPromptCachingEffectiveness:
    """P3: 缓存有效性检测 — 实际服务模型 + 缓存字段观测"""

    def _provider(self):
        from noteforge.core.llm_providers import ClaudeProvider
        return ClaudeProvider({'api_key': 'test'})

    def _call(self, provider, model='claude-sonnet-4-20250514',
              cache_creation=0, cache_read=0):
        """模拟一次成功的生成响应并调用 _track_usage"""
        data = {
            'model': model,
            'usage': {
                'input_tokens': 1000,
                'output_tokens': 100,
                'cache_creation_input_tokens': cache_creation,
                'cache_read_input_tokens': cache_read,
            },
        }
        provider._track_usage(data, 'claude')

    def test_served_model_detected_when_routed(self):
        """代理路由到不同模型时，_served_model 应被捕获"""
        provider = self._provider()
        self._call(provider, model='deepseek-v4-flash')
        assert provider._served_model == 'deepseek-v4-flash'

    def test_served_model_matches_request_no_warning(self):
        """请求模型与响应模型一致 → 不设 _served_model（保持空）"""
        provider = self._provider()
        self._call(provider, model='claude-sonnet-4-20250514')
        assert provider._served_model == ''

    def test_cache_creation_observed_suppresses_warning(self):
        """真实 Anthropic 直接调用：首次即返回 cache_creation → 不警告"""
        provider = self._provider()
        provider._cache_control_requested = True
        self._call(provider, cache_creation=1000, cache_read=0)
        assert provider._cache_saw_fields is True
        provider._check_caching_effectiveness()
        assert provider._cache_warned is False, "有缓存字段时不应警告"

    def test_cache_read_observed_suppresses_warning(self):
        """代理返回 cache_read（自动前缀缓存命中）→ 不警告"""
        provider = self._provider()
        provider._cache_control_requested = True
        self._call(provider, cache_creation=0, cache_read=0)   # 首次无命中
        self._call(provider, cache_creation=0, cache_read=1920)  # 第二次命中
        assert provider._cache_saw_fields is True
        provider._check_caching_effectiveness()
        assert provider._cache_warned is False

    def test_no_cache_fields_warns_after_two_calls(self):
        """请求了缓存但两轮调用均无缓存字段（真实运行场景）→ 警告一次"""
        provider = self._provider()
        provider._cache_control_requested = True
        self._call(provider)   # 第 1 次：无缓存字段
        provider._check_caching_effectiveness()  # calls=1，尚不触发
        assert provider._cache_warned is False, "首次调用后不应警告"
        self._call(provider)   # 第 2 次：仍无缓存字段
        provider._check_caching_effectiveness()  # calls=2 → 触发
        assert provider._cache_warned is True, "两轮无缓存字段应警告"

    def test_warning_only_once(self):
        """警告只发一次"""
        import logging
        from noteforge.core.llm_providers import ClaudeProvider
        provider = self._provider()
        provider._cache_control_requested = True
        self._call(provider)
        self._call(provider)
        provider._check_caching_effectiveness()
        assert provider._cache_warned is True
        provider._check_caching_effectiveness()  # 再次调用不应重复
        assert provider._cache_warned is True

    def test_serve_model_logged_in_usage_tracking(self):
        """served_model 记录不依赖 cache 字段"""
        provider = self._provider()
        self._call(provider, model='step-3.7-flash', cache_creation=0, cache_read=0)
        assert provider._served_model == 'step-3.7-flash'
        assert provider.get_usage()['input_tokens'] == 1000
