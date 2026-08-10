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

        # P3: 捕获实际服务模型（代理可能把请求路由到其它模型，如 deepseek-v4-flash）
        # 仅当响应模型与请求模型不同时才记录，便于 token 日志与定价按实际模型计算。
        served = data.get('model', '')
        if served and served != getattr(self, 'model', ''):
            if served != self._served_model:
                prev = self._served_model
                self._served_model = served
                if self._declared_served_model:
                    # 配置声明的模型与实际不一致（cc-switch 映射已变）→ 提示但不打扰，实际模型优先
                    if prev:
                        logger.info(
                            "实际服务模型 '%s' 与配置声明 '%s' 不一致，已按实际计价"
                            "（如需消除此提示，请更新 llm_engine_config.yaml 的 served_model）",
                            served, prev,
                        )
                else:
                    # 未声明 → 路由到其它模型是意外，警告一次
                    logger.warning(
                        f"代理把 '{self.__class__.__name__}' 请求路由到模型 "
                        f"'{served}'（请求模型 '{self.model}'）。"
                        f"Anthropic cache_control 指令对该后端不保证生效，"
                        f"成本按实际模型估算。"
                    )

        if response_parser == 'claude':
            self._last_usage = {
                'input_tokens': usage.get('input_tokens', 0),
                'output_tokens': usage.get('output_tokens', 0),
            }
            self._last_cache_creation = usage.get('cache_creation_input_tokens', 0)
            self._last_cache_read = usage.get('cache_read_input_tokens', 0)
            self._total_cache_creation += self._last_cache_creation
            self._total_cache_read += self._last_cache_read
            # P3: 观测后端是否真的返回缓存字段（区分「显式缓存生效」与「后端不识别」）
            if self._last_cache_creation > 0 or self._last_cache_read > 0:
                self._cache_saw_fields = True
        else:
            self._last_usage = {
                'input_tokens': usage.get('prompt_tokens', 0),
                'output_tokens': usage.get('completion_tokens', 0),
                'cached_tokens': 0,
            }
        self._total_usage['input_tokens'] += self._last_usage['input_tokens']
        self._total_usage['output_tokens'] += self._last_usage['output_tokens']
        self._total_usage['calls'] += 1

        # P0-1: Token 估算遥测 — 记录实际 token 使用量，用于校准截断策略
        # 当 input_tokens 可用时，与字符数估算对比，发现偏差
        input_tokens = self._last_usage.get('input_tokens', 0)
        if input_tokens > 0 and hasattr(self, '_last_input_chars'):
            estimated = self._last_input_chars / 2.0  # 粗估 2 chars/token
            ratio = input_tokens / estimated if estimated > 0 else 0
            # 偏差超过 20% 时记录警告（帮助发现 CJK 文本的实际 chars/token 比率）
            if ratio > 1.2 or ratio < 0.8:
                logger.debug(
                    f"Token 估算偏差: 实际={input_tokens}, 估算={estimated:.0f}, "
                    f"比率={ratio:.2f} (chars/token={self._last_input_chars/input_tokens:.1f})"
                )


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

    # Risk-6: Provider 适配配置
    # 不同模型/Provider 的行为差异通过 _PROVIDER_PROFILE 配置
    # 包括：内容过滤阈值、context window、默认温度等
    _PROVIDER_PROFILE = {
        'filter_short_threshold': 20,    # 短文本过滤阈值（字符数）
        'filter_medium_threshold': 200,  # 中等文本过滤阈值
        'context_limit': 200000,         # 默认 context window
        'default_temperature': 0.3,      # 默认温度
        'supports_caching': False,       # 是否支持 prompt caching
    }

    def __init__(self):
        # Token 使用量追踪（最近一次调用）
        self._last_usage: dict = {'input_tokens': 0, 'output_tokens': 0}
        # 累计 token 使用量
        self._total_usage: dict = {'input_tokens': 0, 'output_tokens': 0, 'calls': 0}
        # 安全过滤统计（用于自适应调整）
        self._filter_hits: int = 0       # 触发过滤次数
        self._filter_false_pos: int = 0  # 重试后成功的次数（误判）
        # P0-2: Provider-native 信号（从 API 响应提取，比 post-hoc 字符串匹配更可靠）
        self._last_stop_reason: str = ""  # Claude: stop_reason, OpenAI: finish_reason
        self._last_input_chars: int = 0   # P0-1: 输入字符数（遥测用）

        # P3: Prompt Caching 真实有效性检测
        # 发送 cache_control 不代表缓存生效 —— 若经代理路由到非 Anthropic 后端
        # （如 deepseek-v4-flash），后端不识别 Anthropic 显式缓存指令，
        # 只按自身规则做自动前缀缓存（LRU，且与其它共享流量竞争）。
        # 通过观测后端是否返回 cache_creation/cache_read 字段来判断真实命中。
        self._cache_control_requested: bool = False  # 本 provider 是否请求了显式缓存
        self._cache_saw_fields: bool = False         # 是否观察到后端返回缓存字段
        self._cache_warned: bool = False             # 是否已发出「缓存未生效」警告
        self._served_model: str = ""                 # 响应中实际服务的模型（可能≠请求模型）
        self._declared_served_model: bool = False    # 用户是否在配置声明了实际服务模型（cc-switch）

    def get_profile(self) -> dict:
        """获取 Provider 适配配置（Risk-6: 多 Provider 阈值校准）

        不同 Provider 的模型行为差异（如内容过滤敏感度、context window 大小）
        通过此配置统一暴露，下游代码根据 profile 调整行为。

        Returns:
            Provider 适配配置 dict
        """
        return dict(self._PROVIDER_PROFILE)

    def _is_content_filtered(self, text: str) -> bool:
        """检测模型返回是否被内容安全过滤（自适应阈值 + Provider 适配）

        根据历史误判率动态调整：
        - 误判率 > 50%：几乎禁用过滤（仅检查 <10 字）
        - 误判率 > 20%：放宽阈值到 <30 字
        - 默认：使用 Provider profile 阈值 + 拒绝模式检查

        Risk-6: 阈值从 _PROVIDER_PROFILE 读取，不同 Provider 可配置不同阈值。
        """
        if not text:
            return True

        text_len = len(text.strip())
        # Risk-6: 从 Provider profile 读取阈值
        short_threshold = self._PROVIDER_PROFILE.get('filter_short_threshold', 20)
        medium_threshold = self._PROVIDER_PROFILE.get('filter_medium_threshold', 200)

        # 自适应阈值
        if self._filter_hits > 0:
            false_pos_rate = self._filter_false_pos / self._filter_hits
            if false_pos_rate > 0.5:
                return text_len < 10  # 几乎禁用
            elif false_pos_rate > 0.2:
                return text_len < 30  # 放宽

        # 默认阈值（Risk-6: 使用 Provider profile 配置）
        if text_len < short_threshold:
            return True
        if text_len < medium_threshold:
            text_lower = text.lower()
            return any(pat in text_lower for pat in self._FILTER_PATTERNS)
        return False

    def get_usage(self) -> dict:
        """获取最近一次调用的 token 使用量"""
        return self._last_usage.copy()

    def get_total_usage(self) -> dict:
        """获取累计 token 使用量"""
        return self._total_usage.copy()

    def _check_caching_effectiveness(self) -> None:
        """P3: 检查显式缓存的真实有效性（只处理一次）

        请求了 cache_control 但经过 ≥2 次调用后端从未返回缓存字段 →
        说明后端不识别 Anthropic 缓存指令（常见于经代理路由到非 Anthropic 模型）。
        此时 token 日志里的 cached_tokens=0 是诚实的：确实没有缓存发生。

        按运行形态分级提示（P3.1: 适配 cc-switch 代理场景）：
        - 配置已声明实际服务模型（用户已知是第三方模型）→ debug，不打扰
        - 直连真实 Anthropic 但无缓存字段 → warning（真问题，可排查）
        - 代理路由（未声明）→ info，说明成本已按实际模型计价，不推无用建议
        """
        if (self._cache_control_requested
                and not self._cache_saw_fields
                and self._total_usage.get('calls', 0) >= 2
                and not self._cache_warned):
            self._cache_warned = True
            served = self._served_model or '未知（响应未含 model 字段）'
            if self._declared_served_model:
                # 用户已在配置声明实际服务模型（cc-switch 路由到第三方），缓存不生效是预期
                logger.debug(
                    "缓存未生效：配置已声明实际服务模型 '%s'，Anthropic 显式缓存对其"
                    "不保证生效，成本按实际模型计价。", served,
                )
            elif self._using_direct_api:
                # 直连真实 Anthropic：请求了缓存但后端从未返回缓存字段 → 真问题
                logger.warning(
                    "Prompt Caching 未生效：直连 Anthropic 但后端从未返回 "
                    "cache_creation/cache_read（实际服务模型='%s'）。"
                    "请检查 anthropic-version / anthropic-beta header 是否被正确携带。",
                    served,
                )
            else:
                # 代理路由（未声明）：说明现状，不推"获取 Anthropic API Key"类无用建议
                logger.info(
                    "经代理路由到实际模型 '%s'，Anthropic 显式缓存不保证生效，"
                    "成本已按实际服务模型计价（无需额外配置）。", served,
                )

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

    def health_check(self) -> tuple[bool, str]:
        """健康检查：尝试最小 API 调用

        Returns:
            (is_healthy, diagnostic_message)
        """
        try:
            # 用极短 prompt 测试连通性
            self.generate(
                system_prompt="Reply with OK.",
                user_prompt="OK",
                max_tokens=5,
                temperature=0,
            )
            return True, f"{self.get_name()} 可用"
        except LLMError as e:
            return False, f"{self.get_name()} 不可用: {e}"
        except Exception as e:
            return False, f"{self.get_name()} 健康检查异常: {e}"


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

    # Risk-6: Claude Provider 适配配置
    _PROVIDER_PROFILE = {
        'filter_short_threshold': 20,    # Claude 过滤敏感度适中
        'filter_medium_threshold': 200,
        'context_limit': 200000,         # Claude Sonnet 200K
        'default_temperature': 0.3,
        'supports_caching': True,        # 支持 Prompt Caching
    }

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

        # cc-switch 声明式配置：用户在 YAML 声明实际路由到的模型 + context window。
        # 场景：无 Anthropic API，仅经 cc-switch 代理映射到第三方模型（deepseek/step 等）。
        # - served_model: 声明实际服务模型 → 首次调用即按实际模型定价，且不触发"被路由"误导警告
        # - context_limit: 覆盖默认 200K（如 deepseek 上下文可能不同）
        declared = config.get('served_model', '')
        if declared:
            self._served_model = declared
            self._declared_served_model = True
        else:
            self._declared_served_model = False
        self._context_limit = config.get('context_limit', 0)

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
        # P1-3: API 版本固定 — 变更前需运行契约测试
        self._api_version = '2023-06-01'

    def generate(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 8192, temperature: float = 0.3) -> str:
        url = f"{self.base_url}/v1/messages"
        headers = {
            'x-api-key': self.api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
            'anthropic-beta': 'prompt-caching-2024-07-31',
        }
        # P3: 本次请求请求了显式缓存（用于事后校验后端是否真的识别）
        self._cache_control_requested = True
        # P1-3: anthropic-version 固定 — 防止 API 变更导致解析失败
        # 变更此版本号前需运行契约测试验证响应格式兼容性
        self._api_version = '2023-06-01'
        # P0-1: 记录输入字符数，用于 token 估算遥测
        self._last_input_chars = len(system_prompt) + len(user_prompt)
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
            result = self._call_with_retry(url, headers, payload)
            # P3: 校验缓存真实有效性（只在第 ≥2 次调用且从未命中时警告一次）
            self._check_caching_effectiveness()
            return result
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
        # 支持配置覆盖：cc-switch 路由到第三方模型时上下文可能≠Claude 200K
        if self._context_limit > 0:
            return self._context_limit
        return 200000  # Claude Sonnet 200K context

    def get_name(self) -> str:
        return f"Claude ({self.model})"

    def health_check(self) -> tuple[bool, str]:
        """Claude 健康检查：POST 最小 payload 到 Messages API"""
        url = f"{self.base_url}/v1/messages"
        headers = {
            'x-api-key': self.api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        }
        payload = {
            'model': self.model,
            'max_tokens': 5,
            'messages': [{'role': 'user', 'content': 'OK'}],
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            # 200 = 完全可用; 401/403 = key 问题; 429 = 可用但限流; 5xx = 服务端问题
            if resp.status_code == 200:
                return True, f"Claude ({self.model}) 可用"
            if resp.status_code in (401, 403):
                return False, f"Claude API key 无效 (HTTP {resp.status_code})"
            if resp.status_code == 429:
                return True, f"Claude ({self.model}) 可用（限流中）"
            if resp.status_code >= 500:
                return False, f"Claude 服务端错误 (HTTP {resp.status_code})"
            # 其他状态码（如 400）说明连通但参数问题，视为可用
            return True, f"Claude ({self.model}) 连通 (HTTP {resp.status_code})"
        except requests.ConnectionError as e:
            return False, f"Claude 连接失败: {e}"
        except requests.Timeout:
            return False, "Claude 健康检查超时"
        except Exception as e:
            return False, f"Claude 健康检查异常: {e}"

    def _parse_200(self, resp, url):
        data = resp.json()
        # P0-2: 提取 provider-native 信号
        self._last_stop_reason = data.get('stop_reason', '')
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
            # P0-2: 使用 provider-native 信号检测内容过滤
            # Claude stop_reason: "end_turn"=正常, "max_tokens"=截断,
            # "stop_sequence"=命中停止序列, "tool_use"=工具调用
            # 无 stop_reason 或异常值时回退到 _is_content_filtered
            if self._last_stop_reason == 'end_turn':
                pass  # 正常完成，无需额外检查
            elif self._last_stop_reason in ('max_tokens', 'stop_sequence', 'tool_use'):
                pass  # 非过滤原因的停止，无需额外检查
            elif self._is_content_filtered(text):
                raise LLMError("内容安全过滤: 模型拒绝生成", status_code=200, retryable=True)
            self._track_usage(data, 'claude')
            return text
        self._filter_hits += 1
        raise LLMError("Claude 返回空内容")

    def _compute_backoff(self, attempt, status_code):
        return min(self.base_delay * (2 ** attempt), self.max_delay)


class OpenAIProvider(LLMProvider):
    """OpenAI API 提供商"""

    # Risk-6: OpenAI Provider 适配配置
    _PROVIDER_PROFILE = {
        'filter_short_threshold': 30,    # OpenAI 过滤更敏感，提高阈值
        'filter_medium_threshold': 300,  # OpenAI 短文本更易触发过滤
        'context_limit': 128000,         # GPT-4o 128K
        'default_temperature': 0.3,
        'supports_caching': False,       # 不支持 Prompt Caching
    }

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
        # P0-1: 记录输入字符数，用于 token 估算遥测
        self._last_input_chars = len(system_prompt) + len(user_prompt)
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

    def health_check(self) -> tuple[bool, str]:
        """OpenAI 健康检查：POST 最小 payload 到 Chat Completions API"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': self.model,
            'max_tokens': 5,
            'messages': [{'role': 'user', 'content': 'OK'}],
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            if resp.status_code == 200:
                return True, f"OpenAI ({self.model}) 可用"
            if resp.status_code in (401, 403):
                return False, f"OpenAI API key 无效 (HTTP {resp.status_code})"
            if resp.status_code == 429:
                return True, f"OpenAI ({self.model}) 可用（限流中）"
            if resp.status_code >= 500:
                return False, f"OpenAI 服务端错误 (HTTP {resp.status_code})"
            return True, f"OpenAI ({self.model}) 连通 (HTTP {resp.status_code})"
        except requests.ConnectionError as e:
            return False, f"OpenAI 连接失败: {e}"
        except requests.Timeout:
            return False, "OpenAI 健康检查超时"
        except Exception as e:
            return False, f"OpenAI 健康检查异常: {e}"

    _RETRY_MAX: int = 3
    _RETRY_BASE_DELAY: float = 10.0
    _RETRY_MAX_DELAY: float = 120.0
    _HTTP_TIMEOUT: int = 300

    def _parse_200(self, resp, url):
        data = resp.json()
        self._track_usage(data, 'openai')
        choices = data.get('choices', [])
        if choices:
            choice = choices[0]
            # P0-2: 提取 provider-native 信号
            self._last_stop_reason = choice.get('finish_reason', '')
            text = choice.get('message', {}).get('content', '')
            # P0-2: 使用 finish_reason 检测内容过滤
            # OpenAI finish_reason: "stop"=正常, "length"=截断,
            # "content_filter"=内容过滤（provider-native 信号！）
            if self._last_stop_reason == 'content_filter':
                if self._filter_hits < 3:
                    self._filter_hits += 1
                    raise _RetryRequest
                logger.warning("OpenAI content_filter 信号确认，内容被过滤")
            elif self._is_content_filtered(text):
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

    # Risk-6: Local Provider 适配配置
    _PROVIDER_PROFILE = {
        'filter_short_threshold': 15,    # 本地模型通常不过滤，降低阈值
        'filter_medium_threshold': 150,
        'context_limit': 32000,          # 本地模型默认 32K
        'default_temperature': 0.3,
        'supports_caching': False,
    }

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

    def health_check(self) -> tuple[bool, str]:
        """本地模型健康检查：尝试连接 base_url"""
        try:
            resp = requests.get(self.base_url.replace('/v1', ''), timeout=5)
            return True, f"Local ({self.model}) 可用"
        except requests.ConnectionError:
            return False, f"本地模型不可达: {self.base_url}"
        except requests.Timeout:
            return False, f"本地模型响应超时: {self.base_url}"
        except Exception as e:
            # Ollama 等可能返回非标准响应，只要能连上就算可用
            return True, f"Local ({self.model}) 连通（响应异常但端口可达）"

    _RETRY_MAX: int = 3
    _RETRY_BASE_DELAY: float = 15.0
    _RETRY_MAX_DELAY: float = 180.0
    _HTTP_TIMEOUT: int = 600  # 本地模型可能更慢

    def _parse_200(self, resp, url):
        data = resp.json()
        self._track_usage(data, 'openai')
        choices = data.get('choices', [])
        if choices:
            choice = choices[0]
            # P0-2: 提取 provider-native 信号
            self._last_stop_reason = choice.get('finish_reason', '')
            text = choice.get('message', {}).get('content', '')
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
