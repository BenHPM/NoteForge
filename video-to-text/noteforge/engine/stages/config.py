# -*- coding: utf-8 -*-
"""NoteForge 生成阶段配置"""

from dataclasses import dataclass, field
from typing import Dict, FrozenSet


# 事实性内容类型：重试时冻结温度（高温增加幻觉风险）
# P1-2: 默认值，可被 note_generation_rules.yaml 的 temperature_policy 覆盖
FACTUAL_CONTENT_TYPES: FrozenSet[str] = frozenset({
    'lecture', 'interview', 'podcast',
})


@dataclass
class GenerationConfig:
    """生成阶段质量/重试配置（替代散列参数）"""

    max_retries: int = 2
    retry_temp_delta: float = 0.1
    base_temperature: float = 0.3
    min_score: float = 0.80
    save_intermediate: bool = False
    logs_dir: str = ""
    # P0: 事实性内容类型冻结温度（默认启用）
    freeze_temp_for_factual: bool = True
    # P1-2: 温度策略配置（从 YAML 加载，覆盖 FACTUAL_CONTENT_TYPES）
    # 格式: {content_type: "freeze" | "increment" | "adaptive"}
    temperature_policy: Dict[str, str] = field(default_factory=dict)

    def get_temperature_policy(self, content_type: str) -> str:
        """获取指定内容类型的温度策略

        Args:
            content_type: 内容类型

        Returns:
            "freeze" | "increment" | "adaptive"
        """
        # 优先使用 YAML 配置的策略
        if self.temperature_policy and content_type in self.temperature_policy:
            return self.temperature_policy[content_type]
        # 回退：FACTUAL_CONTENT_TYPES 用 freeze，其他用 increment
        if content_type in FACTUAL_CONTENT_TYPES:
            return "freeze"
        return "increment"

    def should_freeze_temperature(self, content_type: str) -> bool:
        """判断指定内容类型是否应冻结重试温度

        Args:
            content_type: 内容类型

        Returns:
            True = 冻结温度（不递增），False = 允许递增
        """
        if not self.freeze_temp_for_factual:
            return False
        return self.get_temperature_policy(content_type) == "freeze"
