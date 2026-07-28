# -*- coding: utf-8 -*-
"""NoteForge 生成阶段配置"""

from dataclasses import dataclass


@dataclass
class GenerationConfig:
    """生成阶段质量/重试配置（替代散列参数）"""

    max_retries: int = 2
    retry_temp_delta: float = 0.1
    base_temperature: float = 0.3
    min_score: float = 0.80
    save_intermediate: bool = False
    logs_dir: str = ""
