# -*- coding: utf-8 -*-
"""NoteForge Pipeline 阶段"""

from noteforge.engine.stages.base import PipelineStage
from noteforge.engine.stages.generate import GenerateStage
from noteforge.engine.stages.preprocess import PreprocessStage
from noteforge.engine.stages.format import FormatStage
from noteforge.engine.stages.save import SaveStage
from noteforge.engine.stages.evaluate import QualityGateStage
from noteforge.engine.stages.postprocess import PostProcessStage

__all__ = [
    'PipelineStage',
    'GenerateStage',
    'PreprocessStage',
    'FormatStage',
    'SaveStage',
    'QualityGateStage',
    'PostProcessStage',
]
