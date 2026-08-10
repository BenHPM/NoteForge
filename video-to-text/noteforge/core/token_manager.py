# -*- coding: utf-8 -*-
"""
NoteForge Token 管理模块
功能:
- 追踪每次 LLM 调用的 token 消耗
- 预估生成成本
- 优化 prompt 大小（缓存提示、分层注入）
- 生成 token 使用报告
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass, field, asdict

logger = logging.getLogger('noteforge.token')


@dataclass
class TokenUsage:
    """单次调用的 token 使用记录"""
    episode: str                # 集数标识
    input_tokens: int           # 输入 tokens
    output_tokens: int          # 输出 tokens
    cached_tokens: int = 0      # 缓存命中 tokens（prompt caching）
    model: str = ""             # 模型名
    timestamp: str = ""         # 时间戳
    purpose: str = "generate"   # 用途：generate / retry / evaluate
    cost_usd: float = 0.0       # 估算成本（美元）
    served_model: str = ""      # 实际服务模型（代理路由后可能≠model）

    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TokenBudget:
    """Token 追踪配置（仅用于统计，不做预算限制）"""
    max_input_tokens: int = 50000      # 单次最大输入（参考值）
    max_output_tokens: int = 8192      # 单次最大输出（参考值）


# 模型定价（美元/百万 tokens）— 仅在线 API
MODEL_PRICING = {
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0, "cached_input": 0.30},
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0, "cached_input": 1.50},
    "claude-haiku-4-20250506": {"input": 0.80, "output": 4.0, "cached_input": 0.08},
    "gpt-4o": {"input": 2.50, "output": 10.0, "cached_input": 1.25},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cached_input": 0.075},
    # P3: 经 cc-switch 代理实际路由到的主流第三方模型（近似价，按命中/未命中区分）
    # 注意：这些模型不识别 Anthropic cache_control，只靠自动前缀缓存（LRU）
    "deepseek-v4-flash": {"input": 0.27, "output": 0.80, "cached_input": 0.03},
    "deepseek-v4": {"input": 0.27, "output": 0.80, "cached_input": 0.03},
    "deepseek": {"input": 0.27, "output": 0.80, "cached_input": 0.03},  # 族级兜底
    "step-3.7-flash": {"input": 0.40, "output": 1.50, "cached_input": 0.05},
    # 默认值
    "default": {"input": 3.0, "output": 15.0, "cached_input": 0.30},
}


class TokenManager:
    """Token 使用追踪和成本管理"""

    def __init__(self, log_dir: str = "output/logs",
                 budget: Optional[TokenBudget] = None,
                 pricing_overrides: Optional[Dict] = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.budget = budget or TokenBudget()
        # 配置驱动的定价覆盖（llm_engine_config.yaml -> model_pricing）。
        # 允许用户为内置表未收录的模型声明定价，切换模型无需改代码。
        # 解析时与 MODEL_PRICING 合并，覆盖优先。
        self._pricing_overrides: Dict = pricing_overrides or {}
        self._usage_log: List[TokenUsage] = []
        self._total_cost: float = 0.0
        self._session_file = self.log_dir / f"token_usage_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    def _pricing_table(self) -> dict:
        """合并内置定价 + 配置覆盖（覆盖优先）。无覆盖时直接返回内置表（零拷贝）。"""
        if not self._pricing_overrides:
            return MODEL_PRICING
        return {**MODEL_PRICING, **self._pricing_overrides}

    def _resolve_pricing(self, model: str, served_model: str = "") -> dict:
        """按实际服务模型解析定价（P3: 代理路由后 model 可能≠实际模型）

        优先 served_model；未收录时按族前缀回退（如 deepseek-v4-flash → deepseek）；
        再回退到请求模型；最后默认价。
        定价表 = 内置 MODEL_PRICING + 配置 model_pricing 覆盖。
        """
        table = self._pricing_table()
        if served_model:
            if served_model in table:
                return table[served_model]
            for family in ('deepseek', 'gpt', 'claude'):
                if served_model.startswith(family) and family in table:
                    return table[family]
        return table.get(model, table["default"])

    def record(self, usage: TokenUsage) -> TokenUsage:
        """记录一次 token 使用"""
        # 计算成本（P3: 优先用实际服务模型定价）
        pricing = self._resolve_pricing(usage.model, usage.served_model)
        if usage.cached_tokens > 0:
            # 有缓存命中时的计算
            uncached_input = usage.input_tokens - usage.cached_tokens
            cost = (
                uncached_input * pricing["input"] / 1_000_000
                + usage.cached_tokens * pricing["cached_input"] / 1_000_000
                + usage.output_tokens * pricing["output"] / 1_000_000
            )
        else:
            cost = (
                usage.input_tokens * pricing["input"] / 1_000_000
                + usage.output_tokens * pricing["output"] / 1_000_000
            )
        usage.cost_usd = round(cost, 6)
        if not usage.timestamp:
            usage.timestamp = datetime.now().isoformat()

        self._usage_log.append(usage)
        self._total_cost += cost

        # 持久化
        self._save_log()

        return usage

    def estimate_cost(self, input_tokens: int, output_tokens: int,
                      model: str = "claude-sonnet-4-20250514",
                      cached_tokens: int = 0,
                      served_model: str = "") -> float:
        """预估一次调用的成本"""
        pricing = self._resolve_pricing(model, served_model)
        if cached_tokens > 0:
            uncached = input_tokens - cached_tokens
            return (
                uncached * pricing["input"] / 1_000_000
                + cached_tokens * pricing["cached_input"] / 1_000_000
                + output_tokens * pricing["output"] / 1_000_000
            )
        return (
            input_tokens * pricing["input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
        )

    def get_summary(self) -> dict:
        """获取当前 session 的 token 使用汇总"""
        if not self._usage_log:
            return {"total_cost": 0, "episodes": 0}

        by_episode = {}
        for u in self._usage_log:
            ep = u.episode
            if ep not in by_episode:
                by_episode[ep] = {"input": 0, "output": 0, "cached": 0, "cost": 0, "calls": 0}
            by_episode[ep]["input"] += u.input_tokens
            by_episode[ep]["output"] += u.output_tokens
            by_episode[ep]["cached"] += u.cached_tokens
            by_episode[ep]["cost"] += u.cost_usd
            by_episode[ep]["calls"] += 1

        return {
            "total_cost": round(self._total_cost, 4),
            "total_input": sum(u.input_tokens for u in self._usage_log),
            "total_output": sum(u.output_tokens for u in self._usage_log),
            "total_cached": sum(u.cached_tokens for u in self._usage_log),
            "episodes": len(by_episode),
            "calls": len(self._usage_log),
            "by_episode": by_episode,
        }

    def print_summary(self):
        """打印 token 使用汇总"""
        s = self.get_summary()
        if s["episodes"] == 0:
            print("No token usage recorded.")
            return

        print("=" * 70)
        print("  Token Usage Summary")
        print("=" * 70)
        print(f"  Total cost: ${s['total_cost']:.4f}")
        print(f"  Total input: {s['total_input']:,} tokens")
        print(f"  Total output: {s['total_output']:,} tokens")
        if s['total_cached'] > 0:
            print(f"  Total cached: {s['total_cached']:,} tokens")
        print(f"  Episodes: {s['episodes']}, Calls: {s['calls']}")
        print()

        print(f"  {'Episode':<20} {'Calls':>6} {'Input':>10} {'Output':>10} {'Cost':>10}")
        print("  " + "-" * 58)
        for ep, data in sorted(s['by_episode'].items()):
            print(
                f"  {ep[:18]:<20} {data['calls']:>6} "
                f"{data['input']:>10,} {data['output']:>10,} "
                f"${data['cost']:>8.4f}"
            )
        print("=" * 70)

    def _save_log(self):
        """持久化 token 使用日志"""
        data = {
            "session_start": self._session_file.stem,
            "summary": self.get_summary(),
            "records": [asdict(u) for u in self._usage_log],
        }
        with open(self._session_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
