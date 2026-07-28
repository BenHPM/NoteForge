# -*- coding: utf-8 -*-
"""
NoteForge Pipeline 阶段基类

所有 pipeline 阶段必须实现 execute() 和 name 属性。
生成阶段配置见 config.py。
"""

from abc import ABC, abstractmethod

from noteforge.context import PipelineContext


class PipelineStage(ABC):
    """流水线阶段基类"""

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> PipelineContext:
        """执行阶段逻辑，返回修改后的上下文"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """阶段名称（用于日志）"""
        pass
