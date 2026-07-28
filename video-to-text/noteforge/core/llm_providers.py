"""
NoteForge LLM 提供商抽象层 v1.0
功能:
- 统一的 LLM 调用接口
- 支持 Claude / OpenAI / 本地模型（Ollama 等）
- 纯 requests 实现，零额外依赖
- 内置指数退避重试
"""

import os
import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, Callable, Any

import requests

logger = logging.getLogger('noteforge.llm')


# ---- RetryMixin：通用重试框架 ----

class RetryMixin:
    """提供统一的 _call_with_retry 基类实现。

    子类通过类属性或实例属性配置重试行为：
    - _RETRY_MAX: 最大重试次数（默认 3）
    - _RETRY_BASE_DELAY: 基础退避秒数（默认 10）
    - _RETRY_MAX_DELAY: 最大退避秒数（默认 120）
    - _RETRY_STATUS_CODES: 可重试的 HTTP 状态码集合
    - _parse_response: (resp, url) -> str，从 HTTP 响应提取文本
    - _compute_backoff: (attempt, status_code) -> float，计算退避秒数
    - _on_filter: (text) -> bool | None，True=继续重试，False=返回文本，None=抛异常
    """

    _RETRY_MAX: int = 3
    _RETRY_BASE_DELAY: float = 10.0
    _RETRY_MAX_DELAY: float = 120.0
    _RETRY_STATUS_CODES: frozenset = frozenset({429, 500, 502, 503})
    _HTTP_TIMEOUT: int = 300

    def _call_with_retry(self, url: str, headers: dict, payload: dict) -> str:
        """带指数退避的重试调用（子类自定义解析和退避策略）"""
        last_error = None
        max_retries = getattr(self, 'max_retries', self._RETRY_MAX)
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    url, headers=headers, json=payload,
                    timeout=self._HTTP_TIMEOUT,
                )

                if resp.status_code == 200:
                    try:
                        return self._parse_200(resp, url)
                    except _RetryRequest:
                        wait = self._compute_backoff(attempt, resp.status_code)
                        logger.warning(f"内容被过滤，{wait}s 后重试 ({attempt + 1}/{max_retries})")
                        time.sleep(wait)
                        last_error = LLMError("内容安全过滤，已重试", status_code=200, retryable=True)
                        continue

                if resp.status_code in self._RETRY_STATUS_CODES:
                    wait = self._compute_backoff(attempt, resp.status_code)
                    friendly = {
                        429: "LLM 请求频率过高", 500: "LLM 服务内部错误",
                        502: "LLM 服务网关错误", 503: "LLM 服务暂时繁忙",
                    }.get(resp.status_code, "LLM 调用暂时失败")
                    logger.warning(f"{friendly}，{wait}s 后自动重试 ({attempt + 1}/{max_retries})")
                    time.sleep(wait)
                    last_error = LLMError(
                        f"{self.__class__.__name__} API {resp.status_code}",
                        resp.status_code, retryable=True,
                    )
                    continue

                # 不可重试
                raise LLMError(
                    f"{self.__class__.__name__} API 错误 {resp.status_code}: {resp.text[:200]}",
                    resp.status_code, retryable=False,
                )

            except requests.Timeout:
                wait = self._compute_backoff(attempt, None)
                logger.warning(f"{self.__class__.__name__} 超时，{wait}s 后重试")
                time.sleep(wait)
                last_error = LLMError(f"{self.__class__.__name__} 超时", retryable=True)
            except requests.ConnectionError as e:
                if not self._on_connection_error(e):
                    # 子类选择不重试
                    last_error = LLMError(
                        f"无法连接 ({self.__class__.__name__}): {e}\n"
                        f"请确认服务已启动。",
                        retryable=False
                    )
                    break

        raise last_error or LLMError(f"{self.__class__.__name__} 调用失败（已耗尽重试）")

    def _on_connection_error(self, e: requests.ConnectionError) -> bool:
        """处理 ConnectionError。返回 True=重试，False=立即失败。"""
        wait = self._compute_backoff(0, None)
        logger.warning(f"{self.__class__.__name__} 连接失败，{wait}s 后重试: {e}")
        time.sleep(wait)
        return True

    def _parse_200(self, resp: requests.Response, url: str) -> str:
        """从 200 响应中提取文本。子类重写此方法。"""
        raise NotImplementedError

    def _compute_backoff(self, attempt: int, status_code: int | None) -> float:
        """计算退避秒数。子类重写此方法。"""
        return min(self._RETRY_BASE_DELAY * (2 ** attempt), self._RETRY_MAX_DELAY)

    def _track_usage(self, data: dict, response_parser: str = 'claude') -> None:
        """统一 token 追踪。response_parser: 'claude' | 'openai'"""
        usage = data.get('usage', {})
        if not usage:
            return
        if response_parser == 'claude':
            self._last_usage = {
                'input_tokens': usage.get('input_tokens', 0),
                'output_tokens': usage.get('output_tokens', 0),
            }
            self._last_cache_creation = usage.get('cache_creation_input_tokens', 0)
            self._last_cache_read = usage.get('cache_read_input_tokens', 0)
            self._total_cache_creation += self._last_cache_creation
            self._total_cache_read += self._last_cache_read
        else:
            self._last_usage = {
                'input_tokens': usage.get('prompt_tokens', 0),
                'output_tokens': usage.get('completion_tokens', 0),
                'cached_tokens': 0,
            }
        self._total_usage['input_tokens'] += self._last_usage['input_tokens']
        self._total_usage['output_tokens'] += self._last_usage['output_tokens']
        self._total_usage['calls'] += 1


class _RetryRequest(Exception):
    """Signal to retry the current HTTP attempt (raised from _parse_200)."""


class LLMProvider(RetryMixin, ABC):
    """LLM 提供商抽象基类"""

    # 内容安全过滤关键词（仅匹配明确的拒绝语句，避免误判正常内容）
    _FILTER_PATTERNS = [
        "request was rejected",
        "considered high risk",
        "content policy violation",
        "i cannot",
        "i'm unable to",
        "i am unable to",
        "as an ai",
    ]

    def __init__(self):
        # Token 使用量追踪（最近一次调用）
        self._last_usage: dict = {'input_tokens': 0, 'output_tokens': 0}
        # 累计 token 使用量
        self._total_usage: dict = {'input_tokens': 0, 'output_tokens': 0, 'calls': 0}
        # 安全过滤统计（用于自适应调整）
        self._filter_hits: int = 0       # 触发过滤次数
        self._filter_false_pos: int = 0  # 重试后成功的次数（误判）

    def _is_content_filtered(self, text: str) -> bool:
        """检测模型返回是否被内容安全过滤（自适应阈值）

        根据历史误判率动态调整：
        - 误判率 > 50%：几乎禁用过滤（仅检查 <10 字）
        - 误判率 > 20%：放宽阈值到 <30 字
        - 默认：<20 字 + 拒绝模式检查
        """
        if not text:
            return True

        text_len = len(text.strip())

        # 自适应阈值
        if self._filter_hits > 0:
            false_pos_rate = self._filter_false_pos / self._filter_hits
            if false_pos_rate > 0.5:
                return text_len < 10  # 几乎禁用
            elif false_pos_rate > 0.2:
                return text_len < 30  # 放宽

        # 默认阈值
        if text_len < 20:
            return True
        if text_len < 200:
            text_lower = text.lower()
            return any(pat in text_lower for pat in self._FILTER_PATTERNS)
        return False

    def get_usage(self) -> dict:
        """获取最近一次调用的 token 使用量"""
        return self._last_usage.copy()

    def get_total_usage(self) -> dict:
        """获取累计 token 使用量"""
        return self._total_usage.copy()

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 8192, temperature: float = 0.3) -> str:
        """
        调用 LLM 生成文本

        Args:
            system_prompt: 系统 prompt
            user_prompt: 用户 prompt
            max_tokens: 最大输出 token 数
            temperature: 温度参数

        Returns:
            生成的文本

        Raises:
            LLMError: 调用失败
        """
        pass

    @abstractmethod
    def get_context_limit(self) -> int:
        """返回最大上下文窗口（token）"""
        pass

    @abstractmethod
    def get_name(self) -> str:
        """返回提供商名称"""
        pass


class LLMError(Exception):
    """LLM 调用异常"""

    def __init__(self, message: str, status_code: int = 0,
                 retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class ClaudeProvider(LLMProvider):
    """Claude API 提供商（通过 Anthropic Messages API）"""

    DIRECT_API_URL = "https://api.anthropic.com"

    def __init__(self, config: dict):
        super().__init__()
        self.model = config.get('model', 'claude-sonnet-4-20250514')
        self.base_url = config.get('base_url', self.DIRECT_API_URL)
        self.max_tokens = config.get('max_tokens', 8192)
        self.temperature = config.get('temperature', 0.3)
        # 重试配置（从 api_retry 节或 provider 节读取）
        retry_cfg = config.get('api_retry', {})
        self.max_retries = retry_cfg.get('max_attempts', 3)
        self.base_delay = retry_cfg.get('base_delay', 10)
        self.max_delay = retry_cfg.get('max_delay', 120)

        api_key_env = config.get('api_key_env', 'ANTHROPIC_API_KEY')
        # 优先使用直接配置的 api_key，其次从环境变量读取
        config_key = config.get('api_key', '')
        if config_key and config_key not in ('PROXY_MANAGED', 'PLACEHOLDER', ''):
            self.api_key = config_key
        else:
            self.api_key = os.environ.get(api_key_env, '')
        # 代理模式（cc-switch 等）：代理负责注入 Key，无需本地配置
        if not self.api_key and self.base_url != self.DIRECT_API_URL:
            self.api_key = 'PROXY_MANAGED'
            self._using_direct_api = False
        elif not self.api_key:
            raise LLMError(
                f"环境变量 {api_key_env} 未设置。"
                f"请设置后重试: set {api_key_env}=your-api-key"
            )
        self._using_direct_api = (self.base_url == self.DIRECT_API_URL)
        # Prompt caching 统计
        self._last_cache_creation: int = 0
        self._last_cache_read: int = 0
        self._total_cache_creation: int = 0
        self._total_cache_read: int = 0

    def generate(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 8192, temperature: float = 0.3) -> str:
        url = f"{self.base_url}/v1/messages"
        headers = {
            'x-api-key': self.api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
            'anthropic-beta': 'prompt-caching-2024-07-31',
        }
        # 使用 prompt caching：system prompt 作为可缓存内容块
        payload = {
            'model': self.model,
            'max_tokens': max_tokens,
            'temperature': temperature,
            'extra_body': {'separator': ''},
            'system': [
                {
                    'type': 'text',
                    'text': system_prompt,
                    'cache_control': {'type': 'ephemeral'},
                }
            ],
            'messages': [{'role': 'user', 'content': user_prompt}],
        }

        try:
            return self._call_with_retry(url, headers, payload)
        except (requests.ConnectionError, requests.Timeout) as e:
            # 代理不可达 → 降级到直连 Anthropic API
            if self.base_url != self.DIRECT_API_URL and not self._using_direct_api:
                logger.warning(
                    f"代理 {self.base_url} 不可达，降级到直连 {self.DIRECT_API_URL}"
                )
                self._using_direct_api = True
                url = f"{self.DIRECT_API_URL}/v1/messages"
                return self._call_with_retry(url, headers, payload)
            raise

    def get_context_limit(self) -> int:
        return 200000  # Claude Sonnet 200K context

    def get_name(self) -> str:
        return f"Claude ({self.model})"

    def _parse_200(self, resp, url):
        data = resp.json()
        content = data.get('content', [])
        if content and isinstance(content, list):
            # StepFun 等模型：用 separator:"" 产生 thinking + text 双块
            # 优先取 text 块（thinking 是推理过程，不应作为最终答案）
            text = ''
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'text':
                    text = block.get('text', '')
                    break
            if not text:
                # 回退：只有 thinking 块时（兼容无 separator 的旧响应）
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'thinking':
                        text = block.get('thinking', '')
                        break
            if not text:
                self._filter_hits += 1
                raise LLMError("模型返回空内容", status_code=200, retryable=True)
            if self._is_content_filtered(text):
                raise LLMError("内容安全过滤: 模型拒绝生成", status_code=200, retryable=True)
            self._track_usage(data, 'claude')
            return text
        self._filter_hits += 1
        raise LLMError("Claude 返回空内容")

    def _compute_backoff(self, attempt, status_code):
        return min(self.base_delay * (2 ** attempt), self.max_delay)


class OpenAIProvider(LLMProvider):
    """OpenAI API 提供商"""

    def __init__(self, config: dict):
        super().__init__()
        self.model = config.get('model', 'gpt-4o')
        self.base_url = config.get('base_url', 'https://api.openai.com/v1')
        self.max_tokens = config.get('max_tokens', 8192)
        self.temperature = config.get('temperature', 0.3)

        api_key_env = config.get('api_key_env', 'OPENAI_API_KEY')
        # 优先使用直接配置的 api_key，其次从环境变量读取
        # 忽略占位符值
        config_key = config.get('api_key', '')
        if config_key and config_key not in ('PROXY_MANAGED', 'PLACEHOLDER', ''):
            self.api_key = config_key
        else:
            self.api_key = os.environ.get(api_key_env, '')
        if not self.api_key:
            raise LLMError(
                f"环境变量 {api_key_env} 未设置。"
                f"请设置后重试: set {api_key_env}=your-api-key"
            )

    def generate(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 8192, temperature: float = 0.3) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': self.model,
            'max_tokens': max_tokens,
            'temperature': temperature,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        }

        return self._call_with_retry(url, headers, payload)

    def get_context_limit(self) -> int:
        return 128000  # GPT-4o 128K context

    def get_name(self) -> str:
        return f"OpenAI ({self.model})"

    _RETRY_MAX: int = 3
    _RETRY_BASE_DELAY: float = 10.0
    _RETRY_MAX_DELAY: float = 120.0
    _HTTP_TIMEOUT: int = 300

    def _parse_200(self, resp, url):
        data = resp.json()
        self._track_usage(data, 'openai')
        choices = data.get('choices', [])
        if choices:
            text = choices[0].get('message', {}).get('content', '')
            if self._is_content_filtered(text):
                if self._filter_hits < 3:
                    self._filter_hits += 1
                    raise _RetryRequest
                # filter_hits >= 3：不再重试，直接返回
                logger.warning("安全过滤已达上限，返回原始内容")
            return text
        raise LLMError("OpenAI 返回空内容")

    def _compute_backoff(self, attempt, status_code):
        return 2 ** attempt * 10


class LocalProvider(LLMProvider):
    """本地 LLM 提供商（Ollama / LM Studio / vLLM 等 OpenAI 兼容接口）"""

    def _on_connection_error(self, e):
        # 本地模型 ConnectionError 不重试，直接报错
        return False

    def __init__(self, config: dict):
        super().__init__()
        self.model = config.get('model', 'qwen2.5-72b')
        self.base_url = config.get('base_url', 'http://localhost:11434/v1')
        self.max_tokens = config.get('max_tokens', 8192)
        self.temperature = config.get('temperature', 0.3)
        self.api_key = config.get('api_key', 'not-needed')

    def generate(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 8192, temperature: float = 0.3) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {'Content-Type': 'application/json'}
        if self.api_key and self.api_key != 'not-needed':
            headers['Authorization'] = f'Bearer {self.api_key}'

        payload = {
            'model': self.model,
            'max_tokens': max_tokens,
            'temperature': temperature,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        }

        return self._call_with_retry(url, headers, payload)

    def get_context_limit(self) -> int:
        return 32000  # 本地模型默认 32K，可在配置中调整

    def get_name(self) -> str:
        return f"Local ({self.model})"

    _RETRY_MAX: int = 3
    _RETRY_BASE_DELAY: float = 15.0
    _RETRY_MAX_DELAY: float = 180.0
    _HTTP_TIMEOUT: int = 600  # 本地模型可能更慢

    def _parse_200(self, resp, url):
        data = resp.json()
        self._track_usage(data, 'openai')
        choices = data.get('choices', [])
        if choices:
            text = choices[0].get('message', {}).get('content', '')
            if self._is_content_filtered(text):
                if self._filter_hits < 3:
                    self._filter_hits += 1
                    raise _RetryRequest
                logger.warning("本地模型安全过滤已达上限，返回原始内容")
            return text
        raise LLMError("本地模型返回空内容")

    def _compute_backoff(self, attempt, status_code):
        return 2 ** attempt * 20


def create_provider(config: dict) -> LLMProvider:
    """
    根据配置创建 LLM 提供商实例

    Args:
        config: llm_engine_config.yaml 中的 provider 配置

    Returns:
        LLMProvider 实例

    Raises:
        LLMError: 不支持的提供商类型
    """
    provider_type = config.get('type', 'claude')
    provider_config = config.get(provider_type, {})
    # 传递顶层配置键（api_retry 等）给 provider 实例
    provider_config['api_retry'] = config.get('api_retry', {})

    providers = {
        'claude': ClaudeProvider,
        'openai': OpenAIProvider,
        'local': LocalProvider,
    }

    provider_cls = providers.get(provider_type)
    if not provider_cls:
        raise LLMError(
            f"不支持的 LLM 提供商: {provider_type}。"
            f"可选: {', '.join(providers.keys())}"
        )

    logger.info(f"初始化 LLM 提供商: {provider_type}")
    return provider_cls(provider_config)
