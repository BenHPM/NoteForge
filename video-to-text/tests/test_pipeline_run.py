# -*- coding: utf-8 -*-
"""
NoteForge Pipeline 编排器单元测试

覆盖 noteforge/engine/pipeline.py 的 Pipeline。

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_pipeline_run.py -v
"""
import os
import pytest
from unittest.mock import MagicMock

from noteforge.engine.pipeline import Pipeline
from noteforge.context import PipelineContext


class MockStage:
    """用于测试的 mock stage"""

    def __init__(self, name, execute_fn=None, should_error=False, should_raise=False):
        self._name = name
        self._execute_fn = execute_fn
        self._should_error = should_error
        self._should_raise = should_raise

    @property
    def name(self):
        return self._name

    def execute(self, ctx):
        if self._should_raise:
            raise RuntimeError(f"{self._name} crashed")
        if self._should_error:
            ctx.error = f"{self._name} failed"
            return ctx
        if self._execute_fn:
            return self._execute_fn(ctx)
        return ctx


class TestPipelineAddStage:
    """Pipeline.add_stage 测试"""

    def test_pipeline_add_stage(self):
        """添加 stage 后 stage_names 正确"""
        pipeline = Pipeline()
        pipeline.add_stage(MockStage("stage_a"))
        pipeline.add_stage(MockStage("stage_b"))
        assert pipeline.stage_names == ["stage_a", "stage_b"]


class TestPipelineRun:
    """Pipeline.run 测试"""

    def test_pipeline_run_success(self):
        """所有 stage 成功执行"""
        pipeline = Pipeline()
        pipeline.add_stage(MockStage("stage_a"))
        pipeline.add_stage(MockStage("stage_b"))
        ctx = PipelineContext()
        result = pipeline.run(ctx)
        assert result.error is None

    def test_pipeline_run_error_breaks(self):
        """stage 报错后中断"""
        pipeline = Pipeline()
        pipeline.add_stage(MockStage("stage_a", should_error=True))
        pipeline.add_stage(MockStage("stage_b"))
        ctx = PipelineContext()
        result = pipeline.run(ctx)
        assert result.error is not None
        assert "stage_a" in result.error

    def test_pipeline_run_exception_breaks(self):
        """stage 抛异常后中断"""
        pipeline = Pipeline()
        pipeline.add_stage(MockStage("stage_a", should_raise=True))
        pipeline.add_stage(MockStage("stage_b"))
        ctx = PipelineContext()
        result = pipeline.run(ctx)
        assert result.error is not None
        assert "stage_a" in str(result.error)

    def test_pipeline_empty_stages(self):
        """空 pipeline 直接返回 ctx"""
        pipeline = Pipeline()
        ctx = PipelineContext(title="test")
        result = pipeline.run(ctx)
        assert result.title == "test"
        assert result.error is None

    def test_pipeline_stages_modify_context(self):
        """stage 可以修改 context"""
        def modify_ctx(ctx):
            ctx.title = "modified"
            return ctx

        pipeline = Pipeline()
        pipeline.add_stage(MockStage("modifier", execute_fn=modify_ctx))
        ctx = PipelineContext(title="original")
        result = pipeline.run(ctx)
        assert result.title == "modified"
