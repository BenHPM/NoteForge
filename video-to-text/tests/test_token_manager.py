# -*- coding: utf-8 -*-
"""测试 noteforge.core.token_manager — TokenManager 和 TokenUsage"""

import os
import pytest
import tempfile
from pathlib import Path

from noteforge.core.token_manager import TokenManager, TokenUsage, MODEL_PRICING


class TestTokenUsage:
    """TokenUsage dataclass 测试"""

    def test_initial_values(self):
        """TokenUsage 初始值"""
        usage = TokenUsage(episode="ep01", input_tokens=1000, output_tokens=500)
        assert usage.episode == "ep01"
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.cached_tokens == 0
        assert usage.model == ""
        assert usage.timestamp == ""
        assert usage.purpose == "generate"
        assert usage.cost_usd == 0.0

    def test_total_tokens(self):
        """TokenUsage.total_tokens() 计算"""
        usage = TokenUsage(episode="ep01", input_tokens=3000, output_tokens=800)
        assert usage.total_tokens() == 3800

    def test_add_record_with_model(self):
        """TokenUsage 带模型名创建"""
        usage = TokenUsage(
            episode="ep02", input_tokens=2000, output_tokens=600,
            cached_tokens=500, model="claude-sonnet-4-20250514",
            purpose="retry",
        )
        assert usage.model == "claude-sonnet-4-20250514"
        assert usage.cached_tokens == 500
        assert usage.purpose == "retry"


class TestTokenManager:
    """TokenManager 测试"""

    def _make_manager(self):
        """创建使用临时目录的 TokenManager，避免污染 output/logs"""
        tmp = tempfile.mkdtemp()
        return TokenManager(log_dir=tmp)

    def test_initial_state(self):
        """TokenManager 初始状态"""
        mgr = self._make_manager()
        summary = mgr.get_summary()
        assert summary["total_cost"] == 0
        assert summary["episodes"] == 0

    def test_record_single(self):
        """TokenManager record() 单次记录"""
        mgr = self._make_manager()
        usage = TokenUsage(
            episode="ep01", input_tokens=10000, output_tokens=2000,
            model="claude-sonnet-4-20250514",
        )
        result = mgr.record(usage)

        # 成本应被计算并填入
        assert result.cost_usd > 0
        # 时间戳应被自动填入
        assert result.timestamp != ""
        # 汇总应有 1 个 episode
        summary = mgr.get_summary()
        assert summary["episodes"] == 1
        assert summary["calls"] == 1

    def test_record_with_cached_tokens(self):
        """TokenManager record() 含缓存 token 的成本计算"""
        mgr = self._make_manager()
        usage = TokenUsage(
            episode="ep01", input_tokens=10000, output_tokens=2000,
            cached_tokens=5000, model="claude-sonnet-4-20250514",
        )
        result = mgr.record(usage)

        # 有缓存时成本应低于无缓存
        pricing = MODEL_PRICING["claude-sonnet-4-20250514"]
        expected_uncached = (10000 - 5000) * pricing["input"] / 1_000_000
        expected_cached = 5000 * pricing["cached_input"] / 1_000_000
        expected_output = 2000 * pricing["output"] / 1_000_000
        expected_cost = round(expected_uncached + expected_cached + expected_output, 6)
        assert result.cost_usd == expected_cost

    def test_get_summary_multiple_records(self):
        """多次 record 后汇总正确"""
        mgr = self._make_manager()
        mgr.record(TokenUsage(
            episode="ep01", input_tokens=10000, output_tokens=2000,
            model="claude-sonnet-4-20250514",
        ))
        mgr.record(TokenUsage(
            episode="ep01", input_tokens=5000, output_tokens=1000,
            model="claude-sonnet-4-20250514", purpose="retry",
        ))
        mgr.record(TokenUsage(
            episode="ep02", input_tokens=8000, output_tokens=1500,
            model="claude-sonnet-4-20250514",
        ))

        summary = mgr.get_summary()
        assert summary["episodes"] == 2
        assert summary["calls"] == 3
        assert summary["total_input"] == 23000
        assert summary["total_output"] == 4500
        assert summary["total_cost"] > 0

        # by_episode 汇总
        by_ep = summary["by_episode"]
        assert "ep01" in by_ep
        assert "ep02" in by_ep
        assert by_ep["ep01"]["calls"] == 2
        assert by_ep["ep01"]["input"] == 15000
        assert by_ep["ep02"]["calls"] == 1

    def test_estimate_cost_no_cache(self):
        """estimate_cost() 无缓存预估"""
        mgr = self._make_manager()
        cost = mgr.estimate_cost(
            input_tokens=10000, output_tokens=2000,
            model="claude-sonnet-4-20250514",
        )
        pricing = MODEL_PRICING["claude-sonnet-4-20250514"]
        expected = (
            10000 * pricing["input"] / 1_000_000
            + 2000 * pricing["output"] / 1_000_000
        )
        assert abs(cost - expected) < 1e-10

    def test_estimate_cost_with_cache(self):
        """estimate_cost() 含缓存预估"""
        mgr = self._make_manager()
        cost = mgr.estimate_cost(
            input_tokens=10000, output_tokens=2000,
            model="claude-sonnet-4-20250514", cached_tokens=5000,
        )
        pricing = MODEL_PRICING["claude-sonnet-4-20250514"]
        expected = (
            5000 * pricing["input"] / 1_000_000
            + 5000 * pricing["cached_input"] / 1_000_000
            + 2000 * pricing["output"] / 1_000_000
        )
        assert abs(cost - expected) < 1e-10

    def test_estimate_cost_unknown_model(self):
        """estimate_cost() 未知模型使用 default 定价"""
        mgr = self._make_manager()
        cost = mgr.estimate_cost(
            input_tokens=10000, output_tokens=2000,
            model="unknown-model-v99",
        )
        pricing = MODEL_PRICING["default"]
        expected = (
            10000 * pricing["input"] / 1_000_000
            + 2000 * pricing["output"] / 1_000_000
        )
        assert abs(cost - expected) < 1e-10

    # ============================================================
    # P3: 按实际服务模型定价（代理路由后请求模型≠实际模型）
    # ============================================================
    def test_record_uses_served_model_pricing(self):
        """served_model 存在时按其定价，而非请求模型"""
        mgr = self._make_manager()
        usage = TokenUsage(
            episode="ep01", input_tokens=1000, output_tokens=500,
            model="claude-sonnet-4-20250514", served_model="deepseek-v4-flash",
        )
        result = mgr.record(usage)
        ds = MODEL_PRICING["deepseek-v4-flash"]
        expected = 1000 * ds["input"] / 1_000_000 + 500 * ds["output"] / 1_000_000
        assert abs(result.cost_usd - expected) < 1e-12
        # 远低于按 claude-sonnet-4 计价
        assert result.cost_usd < 0.005

    def test_served_model_family_fallback(self):
        """未收录的 served_model 按族前缀回退（deepseek-xxx → deepseek）"""
        mgr = self._make_manager()
        cost = mgr.estimate_cost(
            input_tokens=1000, output_tokens=500,
            model="claude-sonnet-4-20250514", served_model="deepseek-v4-flash-extra",
        )
        fam = MODEL_PRICING["deepseek"]
        expected = 1000 * fam["input"] / 1_000_000 + 500 * fam["output"] / 1_000_000
        assert abs(cost - expected) < 1e-12

    def test_served_model_absent_uses_request_model(self):
        """无 served_model 时保持请求模型定价（向后兼容）"""
        mgr = self._make_manager()
        cost = mgr.estimate_cost(
            input_tokens=1000, output_tokens=500,
            model="claude-sonnet-4-20250514",
        )
        cl = MODEL_PRICING["claude-sonnet-4-20250514"]
        expected = 1000 * cl["input"] / 1_000_000 + 500 * cl["output"] / 1_000_000
        assert abs(cost - expected) < 1e-12

    def test_served_model_persisted_to_file(self):
        """served_model 应持久化到日志文件"""
        mgr = self._make_manager()
        mgr.record(TokenUsage(
            episode="ep01", input_tokens=1000, output_tokens=500,
            model="claude-sonnet-4-20250514", served_model="deepseek-v4-flash",
        ))
        log_files = list(Path(mgr.log_dir).glob("token_usage_*.json"))
        import json
        with open(log_files[0], 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert data["records"][0]["served_model"] == "deepseek-v4-flash"

    def test_record_persists_to_file(self):
        """record() 持久化到日志文件"""
        mgr = self._make_manager()
        mgr.record(TokenUsage(
            episode="ep01", input_tokens=1000, output_tokens=500,
            model="claude-sonnet-4-20250514",
        ))
        # 检查日志文件存在且内容可解析
        log_files = list(Path(mgr.log_dir).glob("token_usage_*.json"))
        assert len(log_files) == 1

        import json
        with open(log_files[0], 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert "summary" in data
        assert "records" in data
        assert len(data["records"]) == 1
        assert data["records"][0]["episode"] == "ep01"


class TestPricingOverrides:
    """配置驱动的 model_pricing 覆盖 — 切换模型无需改代码"""

    def _manager(self, overrides):
        tmp = tempfile.mkdtemp()
        return TokenManager(log_dir=tmp, pricing_overrides=overrides)

    def test_override_extends_table(self):
        """内置表未收录的模型，配置新增后按其定价"""
        mgr = self._manager({"qwen3-max": {"input": 0.6, "output": 1.2, "cached_input": 0.06}})
        cost = mgr.estimate_cost(input_tokens=1000, output_tokens=500, model="qwen3-max")
        expected = 1000 * 0.6 / 1e6 + 500 * 1.2 / 1e6
        assert abs(cost - expected) < 1e-12

    def test_override_wins_over_builtin(self):
        """覆盖优先于内置表"""
        mgr = self._manager({"deepseek-v4-flash": {"input": 9.9, "output": 9.9, "cached_input": 0.0}})
        cost = mgr.estimate_cost(
            input_tokens=1000, output_tokens=500,
            model="claude-sonnet-4-20250514", served_model="deepseek-v4-flash",
        )
        expected = 1000 * 9.9 / 1e6 + 500 * 9.9 / 1e6
        assert abs(cost - expected) < 1e-12

    def test_override_served_model_recorded(self):
        """served_model 命中配置覆盖 → record 按覆盖价"""
        mgr = self._manager({"glm-4-plus": {"input": 0.8, "output": 2.0, "cached_input": 0.08}})
        result = mgr.record(TokenUsage(
            episode="ep01", input_tokens=1000, output_tokens=500,
            model="claude-sonnet-4-20250514", served_model="glm-4-plus",
        ))
        expected = 1000 * 0.8 / 1e6 + 500 * 2.0 / 1e6
        assert abs(result.cost_usd - expected) < 1e-12

    def test_override_no_leak_between_instances(self):
        """覆盖不跨 TokenManager 实例泄漏"""
        tmp = tempfile.mkdtemp()
        mgr1 = TokenManager(log_dir=tmp, pricing_overrides={"x": {"input": 1, "output": 1, "cached_input": 0}})
        mgr2 = TokenManager(log_dir=tmp)  # 无覆盖
        # mgr2 不应看到 mgr1 的覆盖 → "x" 走 default 定价
        cost = mgr2.estimate_cost(input_tokens=1, output_tokens=1, model="x")
        dflt = MODEL_PRICING["default"]
        assert abs(cost - (1 * dflt["input"] / 1e6 + 1 * dflt["output"] / 1e6)) < 1e-12

    def test_override_absent_builtin_unaffected(self):
        """无覆盖时内置定价不受影响（回归）"""
        mgr = self._make_plain()
        cost = mgr.estimate_cost(input_tokens=1000, output_tokens=500,
                                 model="claude-sonnet-4-20250514")
        cl = MODEL_PRICING["claude-sonnet-4-20250514"]
        assert abs(cost - (1000 * cl["input"] / 1e6 + 500 * cl["output"] / 1e6)) < 1e-12

    def _make_plain(self):
        return TokenManager(log_dir=tempfile.mkdtemp())
