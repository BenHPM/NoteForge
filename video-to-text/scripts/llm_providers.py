"""
NoteForge LLM 提供商抽象层 v1.0
功能:
- 统一的 LLM 调用接口
- 支持 Claude / OpenAI / 本地模型（Ollama 等）
- 纯 requests 实现，零额外依赖
- 内置指数退避重试
"""

import os
import json
import time
import logging
from abc import ABC, abstractmethod
from typing import Optional

import requests

logger = logging.getLogger('noteforge.llm')


class LLMProvider(ABC):
    """LLM 提供商抽象基类"""

    # 内容安全过滤关键词（mimo/Claude/国内模型通用）
    _FILTER_PATTERNS = [
        "request was rejected",
        "high risk",
        "considered high risk",
        "content policy",
        "safety filter",
        "安全过滤",
        "内容违规",
        "敏感内容",
        "拒绝生成",
    ]

    def __init__(self):
        # Token 使用量追踪（最近一次调用）
        self._last_usage: dict = {'input_tokens': 0, 'output_tokens': 0}
        # 累计 token 使用量
        self._total_usage: dict = {'input_tokens': 0, 'output_tokens': 0, 'calls': 0}

    def _is_content_filtered(self, text: str) -> bool:
        """检测模型返回是否被内容安全过滤"""
        if not text or len(text.strip()) < 50:
            return True
        text_lower = text.lower()
        return any(pat in text_lower for pat in self._FILTER_PATTERNS)

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
        self.api_key = config.get('api_key', '') or os.environ.get(api_key_env, '')
        if not self.api_key:
            raise LLMError(
                f"环境变量 {api_key_env} 未设置。"
                f"请设置后重试: set {api_key_env}=your-api-key"
            )
        self._using_direct_api = False

    def generate(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 8192, temperature: float = 0.3) -> str:
        url = f"{self.base_url}/v1/messages"
        headers = {
            'x-api-key': self.api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        }
        payload = {
            'model': self.model,
            'max_tokens': max_tokens,
            'temperature': temperature,
            'system': system_prompt,
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

    def _call_with_retry(self, url: str, headers: dict, payload: dict) -> str:
        """带指数退避的重试调用"""
        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = requests.post(url, headers=headers, json=payload,
                                      timeout=300)

                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get('content', [])
                    if content and isinstance(content, list):
                        text = content[0].get('text', '')
                        # 检测内容安全过滤（mimo/Claude 等模型会返回 200 但内容被替换）
                        if self._is_content_filtered(text):
                            raise LLMError(
                                "内容安全过滤: 模型拒绝生成（可能是敏感话题触发安全策略）",
                                status_code=200, retryable=False,
                            )
                        # 记录 token 使用量
                        usage = data.get('usage', {})
                        if usage:
                            self._last_usage = {
                                'input_tokens': usage.get('input_tokens', 0),
                                'output_tokens': usage.get('output_tokens', 0),
                            }
                            self._total_usage['input_tokens'] += self._last_usage['input_tokens']
                            self._total_usage['output_tokens'] += self._last_usage['output_tokens']
                            self._total_usage['calls'] += 1
                        return text
                    raise LLMError("Claude 返回空内容")

                # 可重试的错误
                if resp.status_code in (429, 500, 502, 503):
                    retry_after = int(resp.headers.get('Retry-After', 0))
                    wait = retry_after or min(self.base_delay * (2 ** attempt), self.max_delay)
                    logger.warning(
                        f"Claude API {resp.status_code}，{wait}s 后重试 "
                        f"(attempt {attempt + 1}/{self.max_retries})"
                    )
                    time.sleep(wait)
                    last_error = LLMError(
                        f"Claude API {resp.status_code}", resp.status_code,
                        retryable=True
                    )
                    continue

                # 不可重试的错误
                raise LLMError(
                    f"Claude API 错误 {resp.status_code}: {resp.text[:200]}",
                    resp.status_code, retryable=False
                )

            except requests.Timeout:
                wait = min(self.base_delay * (2 ** attempt), self.max_delay)
                logger.warning(f"Claude API 超时，{wait}s 后重试")
                time.sleep(wait)
                last_error = LLMError("Claude API 超时", retryable=True)
            except requests.ConnectionError as e:
                wait = min(self.base_delay * (2 ** attempt), self.max_delay)
                logger.warning(f"Claude API 连接失败，{wait}s 后重试: {e}")
                time.sleep(wait)
                last_error = LLMError(
                    f"连接失败: {e}", retryable=True
                )

        raise last_error or LLMError("Claude API 调用失败（已耗尽重试）")


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
        self.api_key = config.get('api_key', '') or os.environ.get(api_key_env, '')
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

    def _call_with_retry(self, url: str, headers: dict, payload: dict,
                          max_retries: int = 3) -> str:
        last_error = None
        for attempt in range(max_retries):
            try:
                resp = requests.post(url, headers=headers, json=payload,
                                      timeout=300)

                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get('choices', [])
                    if choices:
                        return choices[0].get('message', {}).get('content', '')
                    raise LLMError("OpenAI 返回空内容")

                if resp.status_code in (429, 500, 502, 503):
                    retry_after = int(resp.headers.get('Retry-After', 0))
                    wait = retry_after or (2 ** attempt * 10)
                    logger.warning(
                        f"OpenAI API {resp.status_code}，{wait}s 后重试"
                    )
                    time.sleep(wait)
                    last_error = LLMError(
                        f"OpenAI API {resp.status_code}", resp.status_code,
                        retryable=True
                    )
                    continue

                raise LLMError(
                    f"OpenAI API 错误 {resp.status_code}: {resp.text[:200]}",
                    resp.status_code, retryable=False
                )

            except requests.Timeout:
                wait = 2 ** attempt * 15
                logger.warning(f"OpenAI API 超时，{wait}s 后重试")
                time.sleep(wait)
                last_error = LLMError("OpenAI API 超时", retryable=True)
            except requests.ConnectionError as e:
                wait = 2 ** attempt * 10
                logger.warning(f"OpenAI API 连接失败，{wait}s 后重试: {e}")
                time.sleep(wait)
                last_error = LLMError(f"连接失败: {e}", retryable=True)

        raise last_error or LLMError("OpenAI API 调用失败（已耗尽重试）")


class LocalProvider(LLMProvider):
    """本地 LLM 提供商（Ollama / LM Studio / vLLM 等 OpenAI 兼容接口）"""

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

    def _call_with_retry(self, url: str, headers: dict, payload: dict,
                          max_retries: int = 3) -> str:
        last_error = None
        for attempt in range(max_retries):
            try:
                resp = requests.post(url, headers=headers, json=payload,
                                      timeout=600)  # 本地模型可能更慢

                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get('choices', [])
                    if choices:
                        return choices[0].get('message', {}).get('content', '')
                    raise LLMError("本地模型返回空内容")

                raise LLMError(
                    f"本地模型错误 {resp.status_code}: {resp.text[:200]}",
                    resp.status_code
                )

            except requests.Timeout:
                wait = 2 ** attempt * 20
                logger.warning(f"本地模型超时，{wait}s 后重试")
                time.sleep(wait)
                last_error = LLMError("本地模型超时", retryable=True)
            except requests.ConnectionError as e:
                raise LLMError(
                    f"无法连接本地模型 ({self.base_url}): {e}\n"
                    f"请确认本地模型服务已启动。",
                    retryable=False
                )

        raise last_error or LLMError("本地模型调用失败（已耗尽重试）")


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
