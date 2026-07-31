# -*- coding: utf-8 -*-
"""
NoteForge Pipeline 阶段基类

所有 pipeline 阶段必须实现 execute() 和 name 属性。
生成阶段配置见 config.py。

阶段依赖协议：
  - required_inputs: 该阶段执行前 ctx 中必须存在的字段名（frozenset）
  - provided_outputs: 该阶段执行后写入 ctx 的字段名（frozenset）
  - validate_inputs(ctx): 运行前校验 ctx 中 required_inputs 对应字段非空
"""

from abc import ABC, abstractmethod
from typing import FrozenSet

from noteforge.context import PipelineContext


class PipelineStage(ABC):
    """流水线阶段基类

    子类应声明 required_inputs / provided_outputs 以使依赖显式化。
    Pipeline._validate_order() 会据此校验阶段顺序的合法性。

    Attributes:
        required_inputs: 执行前 ctx 中必须已提供的字段名集合
        provided_outputs: 执行后写入 ctx 的字段名集合
    """

    required_inputs: FrozenSet[str] = frozenset()
    provided_outputs: FrozenSet[str] = frozenset()

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> PipelineContext:
        """执行阶段逻辑，返回修改后的上下文"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """阶段名称（用于日志）"""
        pass

    def validate_inputs(self, ctx: PipelineContext) -> None:
        """校验 ctx 中 required_inputs 对应字段非空。

        对于字符串字段：非空字符串即视为有效。
        对于列表字段：非空列表即视为有效。
        对于字典字段：非空字典即视为有效。
        对于数值字段：非零即视为有效。
        对于布尔字段：始终视为有效（布尔值本身就是合法的）。
        对于 None：视为无效。

        Raises:
            ValueError: 若任一 required_inputs 字段为空/None
        """
        missing: list = []
        for field_name in self.required_inputs:
            value = getattr(ctx, field_name, None)
            if value is None:
                missing.append(field_name)
            elif isinstance(value, bool):
                pass  # bool is always valid (False is a legitimate value)
            elif isinstance(value, str) and value == "":
                missing.append(field_name)
            elif isinstance(value, (list, dict)) and len(value) == 0:
                missing.append(field_name)
            elif isinstance(value, (int, float)) and value == 0:
                missing.append(field_name)
        if missing:
            raise ValueError(
                f"Stage '{self.name}' missing required inputs: "
                f"{', '.join(sorted(missing))}"
            )
