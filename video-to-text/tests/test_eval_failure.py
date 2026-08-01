# -*- coding: utf-8 -*-
"""
EvalFailure + FailureClass 单元测试

覆盖：
  - FailureClass 枚举值
  - EvalFailure reason → failure_class 自动推导
  - EvalFailure to_dict 序列化
  - 向后兼容性（retry_eligible 与 failure_class 同步）
"""

import pytest
from noteforge.quality.models import EvalFailure, FailureClass, _REASON_CLASS_MAP


class TestFailureClass:
    """FailureClass 枚举测试"""

    def test_enum_values(self):
        assert FailureClass.RETRYABLE.value == "retryable"
        assert FailureClass.TERMINAL.value == "terminal"
        assert FailureClass.DEGRADED.value == "degraded"

    def test_reason_class_map_covers_all_known_reasons(self):
        """所有已知 reason 应在 _REASON_CLASS_MAP 中注册"""
        known_reasons = {"json_parse", "content_filter", "empty", "timeout",
                         "api_key", "model_error", "other"}
        assert set(_REASON_CLASS_MAP.keys()) == known_reasons

    def test_reason_class_map_no_terminal_in_default_retryable(self):
        """默认 failure_class 是 RETRYABLE，但 reason 映射可能覆盖"""
        # content_filter 应映射为 DEGRADED，不是 RETRYABLE
        assert _REASON_CLASS_MAP["content_filter"] == FailureClass.DEGRADED
        # api_key 应映射为 TERMINAL
        assert _REASON_CLASS_MAP["api_key"] == FailureClass.TERMINAL


class TestEvalFailure:
    """EvalFailure dataclass 测试"""

    def test_auto_derive_json_parse(self):
        ef = EvalFailure(reason="json_parse", raw_response="bad json")
        assert ef.failure_class == FailureClass.RETRYABLE
        assert ef.retry_eligible is True

    def test_auto_derive_content_filter(self):
        ef = EvalFailure(reason="content_filter", raw_response="I cannot")
        assert ef.failure_class == FailureClass.DEGRADED
        assert ef.retry_eligible is False  # DEGRADED → 不重试

    def test_auto_derive_timeout(self):
        ef = EvalFailure(reason="timeout", raw_response="")
        assert ef.failure_class == FailureClass.RETRYABLE
        assert ef.retry_eligible is True

    def test_auto_derive_other(self):
        ef = EvalFailure(reason="other", raw_response="some error")
        assert ef.failure_class == FailureClass.RETRYABLE

    def test_explicit_failure_class_overrides_auto(self):
        """显式指定 failure_class 应覆盖自动推导"""
        ef = EvalFailure(
            reason="json_parse",
            raw_response="bad",
            failure_class=FailureClass.TERMINAL,
        )
        # 显式指定 TERMINAL，但 reason="json_parse" 映射为 RETRYABLE
        # __post_init__ 只在默认 RETRYABLE 时自动推导
        # 由于显式指定了 TERMINAL，不应被覆盖
        assert ef.failure_class == FailureClass.TERMINAL
        assert ef.retry_eligible is False

    def test_unknown_reason_defaults_to_retryable(self):
        """未知 reason 应默认 RETRYABLE"""
        ef = EvalFailure(reason="new_unknown_error", raw_response="x")
        assert ef.failure_class == FailureClass.RETRYABLE
        assert ef.retry_eligible is True

    def test_terminal_syncs_retry_eligible(self):
        """TERMINAL 应同步 retry_eligible=False"""
        ef = EvalFailure(
            reason="api_key",
            raw_response="invalid key",
        )
        assert ef.failure_class == FailureClass.TERMINAL
        assert ef.retry_eligible is False

    def test_to_dict_includes_failure_class(self):
        ef = EvalFailure(reason="json_parse", raw_response="bad", provider="claude")
        d = ef.to_dict()
        assert "failure_class" in d
        assert d["failure_class"] == "retryable"
        assert d["reason"] == "json_parse"
        assert d["retry_eligible"] is True
        assert d["provider"] == "claude"

    def test_to_dict_truncates_raw_response(self):
        ef = EvalFailure(reason="other", raw_response="x" * 1000)
        d = ef.to_dict()
        assert len(d["raw_response"]) <= 500

    def test_degraded_not_retryable(self):
        """DEGRADED 输出不应重试（降级使用当前输出）"""
        ef = EvalFailure(
            reason="content_filter",
            raw_response="filtered",
            failure_class=FailureClass.DEGRADED,
        )
        assert ef.retry_eligible is False


class TestEvalFailureRouting:
    """测试调用方路由逻辑（模拟 auto_pipeline 使用场景）"""

    def test_retryable_triggers_retry(self):
        ef = EvalFailure(reason="json_parse", raw_response="bad")
        # 调用方路由：RETRYABLE → 重试
        assert ef.failure_class == FailureClass.RETRYABLE
        should_retry = ef.failure_class == FailureClass.RETRYABLE
        assert should_retry is True

    def test_terminal_triggers_abort(self):
        ef = EvalFailure(reason="api_key", raw_response="invalid")
        # 调用方路由：TERMINAL → 终止
        should_abort = ef.failure_class == FailureClass.TERMINAL
        assert should_abort is True

    def test_degraded_triggers_use_with_warning(self):
        ef = EvalFailure(reason="content_filter", raw_response="I cannot")
        # 调用方路由：DEGRADED → 降级使用
        should_use_degraded = ef.failure_class == FailureClass.DEGRADED
        assert should_use_degraded is True
