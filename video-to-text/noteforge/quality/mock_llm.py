# -*- coding: utf-8 -*-
"""
MockLLMProvider — 可编程 LLM 提供商测试夹具

P0-4: 用于质量门禁逻辑的确定性回归测试。
支持模拟各种 LLM 响应场景：
  - 正常响应
  - 拒绝/内容过滤
  - 截断输出
  - 幻觉内容
  - JSON 解析失败
  - 空响应
  - 超时/网络错误
"""

import json
import logging
from typing import Optional, List, Dict

from noteforge.core.llm_providers import LLMProvider, LLMError

logger = logging.getLogger('noteforge.test.mock_llm')


class MockResponse:
    """可编程的模拟响应"""
    def __init__(self, text: str, stop_reason: str = 'end_turn',
                 finish_reason: str = 'stop',
                 usage: Optional[dict] = None):
        self.text = text
        self.stop_reason = stop_reason
        self.finish_reason = finish_reason
        self.usage = usage or {'input_tokens': 100, 'output_tokens': 50}


class MockLLMProvider(LLMProvider):
    """可编程 LLM 提供商 — 用于质量门禁逻辑的确定性测试

    用法:
        provider = MockLLMProvider()
        provider.set_response("normal", "这是一篇正常的笔记内容...")
        provider.set_response("refusal", "I cannot complete this request")
        provider.set_response("json_fail", "not valid json {")

        # 切换到特定响应
        provider.use_response("normal")
        result = provider.generate("system", "user")

        # 或按顺序返回
        provider.set_sequence(["normal", "refusal", "normal"])
    """

    def __init__(self, context_limit: int = 200000, name: str = "MockLLM"):
        super().__init__()
        self._context_limit = context_limit
        self._name = name
        self._responses: Dict[str, MockResponse] = {}
        self._sequence: List[str] = []
        self._sequence_index: int = 0
        self._current_key: str = "default"
        self._call_log: List[dict] = []
        self._default_response = MockResponse("默认响应")

    def set_response(self, key: str, text: str,
                     stop_reason: str = 'end_turn',
                     finish_reason: str = 'stop',
                     usage: Optional[dict] = None) -> 'MockLLMProvider':
        """注册一个命名的响应

        Args:
            key: 响应名称（如 "normal", "refusal", "json_fail"）
            text: 响应文本
            stop_reason: Claude stop_reason
            finish_reason: OpenAI finish_reason
            usage: token 使用量

        Returns:
            self（支持链式调用）
        """
        self._responses[key] = MockResponse(
            text, stop_reason, finish_reason, usage
        )
        return self

    def use_response(self, key: str) -> 'MockLLMProvider':
        """切换到指定命名的响应

        Returns:
            self（支持链式调用）
        """
        if key not in self._responses:
            raise ValueError(f"未注册的响应: {key}（已注册: {list(self._responses.keys())}）")
        self._current_key = key
        return self

    def set_sequence(self, keys: List[str]) -> 'MockLLMProvider':
        """设置响应序列（按调用顺序依次返回）

        Args:
            keys: 响应名称列表

        Returns:
            self
        """
        self._sequence = keys
        self._sequence_index = 0
        return self

    def set_error(self, key: str, message: str, retryable: bool = False) -> 'MockLLMProvider':
        """注册一个会抛出 LLMError 的响应

        Args:
            key: 响应名称
            message: 错误消息
            retryable: 是否可重试

        Returns:
            self
        """
        self._responses[key] = MockResponse(
            text=f"__ERROR__:{message}",
            stop_reason="error",
            finish_reason="error",
        )
        self._responses[key]._is_error = True
        self._responses[key]._error_message = message
        self._responses[key]._error_retryable = retryable
        return self

    def generate(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 8192, temperature: float = 0.3) -> str:
        """模拟 LLM 生成（返回预设响应）"""
        # 记录调用日志
        self._call_log.append({
            'system_prompt': system_prompt[:200],
            'user_prompt': user_prompt[:200],
            'max_tokens': max_tokens,
            'temperature': temperature,
        })

        # 确定使用哪个响应
        response = self._get_next_response()

        # 检查是否为错误响应
        if hasattr(response, '_is_error') and response._is_error:
            raise LLMError(
                response._error_message,
                status_code=500,
                retryable=response._error_retryable,
            )

        # 更新 provider 状态
        self._last_stop_reason = response.stop_reason
        self._last_input_chars = len(system_prompt) + len(user_prompt)
        self._last_usage = response.usage or {'input_tokens': 100, 'output_tokens': 50}
        self._total_usage['input_tokens'] += self._last_usage.get('input_tokens', 0)
        self._total_usage['output_tokens'] += self._last_usage.get('output_tokens', 0)
        self._total_usage['calls'] += 1

        return response.text

    def _get_next_response(self) -> MockResponse:
        """获取下一个响应（序列模式或当前 key）"""
        if self._sequence and self._sequence_index < len(self._sequence):
            key = self._sequence[self._sequence_index]
            self._sequence_index += 1
            return self._responses.get(key, self._default_response)
        return self._responses.get(self._current_key, self._default_response)

    def get_context_limit(self) -> int:
        return self._context_limit

    def get_name(self) -> str:
        return self._name

    @property
    def call_count(self) -> int:
        """返回 generate() 被调用的次数"""
        return len(self._call_log)

    @property
    def call_log(self) -> List[dict]:
        """返回调用日志"""
        return self._call_log

    def reset(self) -> 'MockLLMProvider':
        """重置状态（调用日志、序列索引）"""
        self._call_log = []
        self._sequence_index = 0
        self._filter_hits = 0
        self._filter_false_pos = 0
        self._total_usage = {'input_tokens': 0, 'output_tokens': 0, 'calls': 0}
        return self


# ============================================================
# 预定义响应工厂
# ============================================================

def make_normal_note_response() -> str:
    """生成一个正常笔记响应（用于质量门禁通过测试）"""
    return (
        "# 测试视频笔记\n\n"
        "> **课程定位**：测试用笔记，包含完整结构\n\n"
        "---\n\n"
        "## 核心观点\n\n"
        "1. 第一个核心观点是关于测试的，原文明确提到了这一点。\n"
        "2. 第二个观点涉及质量门禁的重要性，讲师强调了其必要性。\n"
        "3. 第三个观点是关于自动化测试的价值，有具体案例支撑。\n\n"
        "## 知识框架提炼\n\n"
        "### 框架 1：质量门禁体系\n"
        "- **核心定义**: 通过多维度规则检查确保输出质量的系统\n"
        "- **组成要素**: R0-R12 规则 + 启发式指标 + LLM 评审\n"
        "- **适用场景**: 任何 LLM 生成内容的质量保障\n\n"
        "## 可迁移洞察\n\n"
        "| 洞察 | 做什么 | 何时用 | 预期效果 |\n"
        "|------|--------|--------|----------|\n"
        "| 质量前置 | 在生成前注入规则 | 每次生成 | 减少返工 |\n\n"
        "## 学习总结\n\n"
        "### 核心收获\n"
        "- 质量门禁是必要的，不能依赖 LLM 自觉\n"
        "- 规则要分层：致命/严重/中等\n\n"
        "### 行动清单\n"
        "- [ ] 为每个项目建立质量门禁 — 每次新项目 | 产出质量配置文件\n"
        "- [ ] 定期审查规则有效性 — 每月 | 更新规则配置\n\n"
        "### 金句摘录\n"
        '> "质量不是测试出来的，是设计出来的" —— 讲师\n\n'
        "---\n\n"
        "*笔记整理时间：2026-08-01*\n"
        "*学习来源：原视频音频转写*\n\n"
        "*转写质量：良好 | 已知问题：无明显噪声 | 人名校对：已校对*"
    )


def make_refusal_response() -> str:
    """生成一个拒绝响应"""
    return "I cannot complete this request as it was considered high risk."


def make_truncated_response() -> str:
    """生成一个截断响应（末尾无标点）"""
    return (
        "# 截断笔记\n\n"
        "## 核心观点\n\n"
        "这是一个被截断的笔记，最后一行没有标点"
    )


def make_hallucination_response() -> str:
    """生成一个包含幻觉数据的响应"""
    return (
        "# 幻觉笔记\n\n"
        "## 核心观点\n\n"
        "1. 市场占比达到65%，这是原文没有的数据。\n"
        "2. 增长了32.5%，原文只说了'增长'没有给数字。\n"
    )


def make_json_eval_response(score: float = 4.0) -> str:
    """生成一个 LLM 评审 JSON 响应"""
    return json.dumps({
        "richness": score,
        "readability": score,
        "faithfulness": score,
        "actionability": score - 0.5,
        "overall": score,
        "feedback": "测试评审反馈",
        "suggestions": ["建议1", "建议2"],
    }, ensure_ascii=False)


def make_json_eval_malformed() -> str:
    """生成一个格式错误的 JSON 评审响应"""
    return '```json\n{"richness": 4.0, "readability": 3.5, "faithfulness": 5.0,}'


def create_mock_provider() -> MockLLMProvider:
    """创建一个预配置了常见响应的 MockLLMProvider

    预注册响应:
      - "normal": 正常笔记
      - "refusal": 拒绝文本
      - "truncated": 截断输出
      - "hallucination": 幻觉数据
      - "json_eval": 正常 LLM 评审 JSON
      - "json_malformed": 格式错误的 JSON
      - "empty": 空响应
      - "timeout": 超时错误
    """
    provider = MockLLMProvider()
    provider.set_response("normal", make_normal_note_response())
    provider.set_response("refusal", make_refusal_response(),
                          stop_reason='end_turn', finish_reason='content_filter')
    provider.set_response("truncated", make_truncated_response())
    provider.set_response("hallucination", make_hallucination_response())
    provider.set_response("json_eval", make_json_eval_response())
    provider.set_response("json_malformed", make_json_eval_malformed())
    provider.set_response("empty", "")
    provider.set_error("timeout", "LLM 调用超时", retryable=True)
    provider.set_error("api_key", "API key 无效", retryable=False)
    return provider
