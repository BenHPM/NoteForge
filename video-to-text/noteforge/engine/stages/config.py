# -*- coding: utf-8 -*-
"""NoteForge 生成阶段配置"""

from dataclasses import dataclass, field
from typing import FrozenSet


# 事实性内容类型：重试时冻结温度（高温增加幻觉风险）
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
