# -*- coding: utf-8 -*-
"""
auto_pipeline 失败策略路由测试

覆盖：
  - resolve_policy: 错误类型 → 策略映射
  - ERROR_POLICY_MAP 完整性
  - 重试次数耗尽升级为 SKIP
  - process_videos 策略路由（集成测试骨架）
"""

import pytest
from noteforge.batch.auto_pipeline import (
    resolve_policy, ERROR_POLICY_MAP,
    POLICY_STOP, POLICY_SKIP, POLICY_DEGRADE, POLICY_ABORT,
    classify_error,
)


class TestResolvePolicy:
    """resolve_policy 策略路由测试"""

    def test_network_error_returns_stop(self):
        assert resolve_policy('network') == POLICY_STOP

    def test_timeout_returns_stop(self):
        assert resolve_policy('timeout') == POLICY_STOP

    def test_deleted_returns_skip(self):
        assert resolve_policy('deleted') == POLICY_SKIP

    def test_too_short_returns_skip(self):
        assert resolve_policy('too_short') == POLICY_SKIP

    def test_content_filter_returns_degrade(self):
        assert resolve_policy('content_filter') == POLICY_DEGRADE

    def test_quality_fatal_returns_stop(self):
        assert resolve_policy('quality_fatal') == POLICY_STOP

    def test_quality_minor_returns_degrade(self):
        assert resolve_policy('quality_minor') == POLICY_DEGRADE

    def test_api_key_returns_abort(self):
        assert resolve_policy('api_key') == POLICY_ABORT

    def test_unknown_returns_stop(self):
        """未知错误应保守选择 STOP"""
        assert resolve_policy('unknown') == POLICY_STOP

    def test_code_bug_returns_stop(self):
        assert resolve_policy('code_bug') == POLICY_STOP

    def test_retry_exhaustion_upgrades_stop_to_skip(self):
        """重试次数耗尽时，STOP 升级为 SKIP"""
        assert resolve_policy('network', retry_count=3, max_retries=3) == POLICY_SKIP
        assert resolve_policy('timeout', retry_count=3, max_retries=3) == POLICY_SKIP

    def test_retry_not_exhausted_stays_stop(self):
        """重试次数未耗尽时，STOP 保持"""
        assert resolve_policy('network', retry_count=2, max_retries=3) == POLICY_STOP

    def test_skip_not_affected_by_retry_count(self):
        """SKIP 策略不受重试次数影响"""
        assert resolve_policy('deleted', retry_count=10, max_retries=3) == POLICY_SKIP

    def test_abort_not_affected_by_retry_count(self):
        """ABORT 策略不受重试次数影响"""
        assert resolve_policy('api_key', retry_count=0, max_retries=3) == POLICY_ABORT

    def test_degrade_not_affected_by_retry_count(self):
        """DEGRADE 策略不受重试次数影响"""
        assert resolve_policy('content_filter', retry_count=5, max_retries=3) == POLICY_DEGRADE


class TestErrorPolicyMap:
    """ERROR_POLICY_MAP 完整性测试"""

    def test_all_classify_error_types_have_policy(self):
        """classify_error 返回的所有类型应有对应策略"""
        # classify_error 的所有可能返回值
        classify_error_outputs = {'timeout', 'network', 'deleted', 'too_short',
                                  'code_bug', 'unknown'}
        for err_type in classify_error_outputs:
            assert err_type in ERROR_POLICY_MAP, f"缺少策略: {err_type}"

    def test_all_policies_are_valid(self):
        """所有策略值应为有效枚举"""
        valid = {POLICY_STOP, POLICY_SKIP, POLICY_DEGRADE, POLICY_ABORT}
        for err_type, policy in ERROR_POLICY_MAP.items():
            assert policy in valid, f"无效策略 {policy} for {err_type}"


class TestClassifyError:
    """classify_error 分类测试"""

    def test_timeout(self):
        assert classify_error('TIMEOUT', '') == 'timeout'
        assert classify_error('', 'timed out') == 'timeout'

    def test_network(self):
        assert classify_error('Connection refused', '') == 'network'
        assert classify_error('getaddrinfo failed', '') == 'network'

    def test_deleted(self):
        assert classify_error('啥都木有', '') == 'deleted'
        assert classify_error('video info', '') == 'deleted'

    def test_too_short(self):
        assert classify_error('转写文本过短', '') == 'too_short'

    def test_code_bug(self):
        assert classify_error('AttributeError: foo', '') == 'code_bug'
        assert classify_error('NameError: bar', '') == 'code_bug'

    def test_unknown(self):
        assert classify_error('something weird', '') == 'unknown'
