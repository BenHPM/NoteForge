# -*- coding: utf-8 -*-
"""
NoteForge Stage 依赖协议测试

覆盖：
- 各 stage 声明 required_inputs / provided_outputs
- Pipeline._validate_order() 基于数据字段的依赖校验
- Pipeline 构造/添加 stage 时 unsatisfied inputs 报错
- PipelineStage.validate_inputs() 运行时校验
"""

import pytest
from unittest.mock import MagicMock
from pathlib import Path

from noteforge.context import PipelineContext
from noteforge.engine.pipeline import Pipeline
from noteforge.engine.stages.base import PipelineStage


# ============================================================
# Helper: concrete PipelineStage for testing
# ============================================================

class DummyStage(PipelineStage):
    """可配置的测试用 stage"""

    def __init__(self, name, required_inputs=frozenset(), provided_outputs=frozenset()):
        self._name = name
        self.required_inputs = required_inputs
        self.provided_outputs = provided_outputs

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        return ctx


# ============================================================
# 1. 各 stage 声明 required_inputs / provided_outputs
# ============================================================

class TestStageDeclarations:
    """验证每个具体 stage 都正确声明了依赖协议"""

    def test_preprocess_stage_declarations(self):
        from noteforge.engine.stages.preprocess import PreprocessStage
        stage = PreprocessStage(MagicMock(), {})
        assert stage.required_inputs == frozenset({"source_path", "transcript_path"})
        assert stage.provided_outputs == frozenset({"raw_text", "clean_text", "chunks"})

    def test_generate_stage_declarations(self):
        from noteforge.engine.stages.generate import GenerateStage
        stage = GenerateStage(MagicMock(), MagicMock(), MagicMock())
        assert stage.required_inputs == frozenset({"clean_text", "chunks", "title", "content_type"})
        assert stage.provided_outputs == frozenset({"note_text", "attempts"})

    def test_format_stage_declarations(self):
        from noteforge.engine.stages.format import FormatStage
        stage = FormatStage(MagicMock())
        assert stage.required_inputs == frozenset({"note_text", "content_type"})
        assert stage.provided_outputs == frozenset({"formatted_text"})

    def test_quality_gate_stage_declarations(self):
        from noteforge.engine.stages.evaluate import QualityGateStage
        stage = QualityGateStage(MagicMock(), reports_dir=Path("/tmp"))
        assert stage.required_inputs == frozenset({"formatted_text", "clean_text"})
        assert stage.provided_outputs == frozenset({"quality_report", "total_score", "overall_passed"})

    def test_save_stage_declarations(self):
        from noteforge.engine.stages.save import SaveStage
        stage = SaveStage(notes_dir=Path("/tmp"))
        assert stage.required_inputs == frozenset({"formatted_text", "output_path", "title"})
        assert stage.provided_outputs == frozenset()

    def test_postprocess_stage_declarations(self):
        from noteforge.engine.stages.postprocess import PostProcessStage
        stage = PostProcessStage()
        assert stage.required_inputs == frozenset({"note_text", "output_path"})
        assert stage.provided_outputs == frozenset()

    def test_base_stage_defaults(self):
        """PipelineStage 基类默认值为空 frozenset"""
        stage = DummyStage("test")
        assert stage.required_inputs == frozenset()
        assert stage.provided_outputs == frozenset()


# ============================================================
# 2. Pipeline validates stage order based on dependencies
# ============================================================

class TestPipelineDependencyValidation:
    """Pipeline._validate_order() 数据依赖校验"""

    def test_valid_full_pipeline(self):
        """标准 6 阶段流水线顺序合法"""
        from noteforge.engine.stages.preprocess import PreprocessStage
        from noteforge.engine.stages.generate import GenerateStage
        from noteforge.engine.stages.format import FormatStage
        from noteforge.engine.stages.evaluate import QualityGateStage
        from noteforge.engine.stages.save import SaveStage
        from noteforge.engine.stages.postprocess import PostProcessStage

        # Should not raise
        pipeline = Pipeline([
            PreprocessStage(MagicMock(), {}),
            GenerateStage(MagicMock(), MagicMock(), MagicMock()),
            FormatStage(MagicMock()),
            QualityGateStage(MagicMock(), reports_dir=Path("/tmp")),
            SaveStage(notes_dir=Path("/tmp")),
            PostProcessStage(),
        ])
        assert pipeline.stage_names == [
            "preprocess", "generate", "format",
            "quality_gate", "save", "postprocess",
        ]

    def test_valid_custom_pipeline(self):
        """自定义 stage 链：A 提供 x，B 需要 x"""
        a = DummyStage("A", required_inputs=frozenset(), provided_outputs=frozenset({"x"}))
        b = DummyStage("B", required_inputs=frozenset({"x"}), provided_outputs=frozenset({"y"}))
        # Should not raise
        pipeline = Pipeline([a, b])
        assert pipeline.stage_names == ["A", "B"]

    def test_ctx_input_fields_satisfy_dependencies(self):
        """PipelineContext 输入字段（title, content_type 等）满足依赖"""
        # A stage that only needs ctx input fields (no prior stage outputs)
        stage = DummyStage("first", required_inputs=frozenset({"title", "content_type"}))
        # Should not raise — title and content_type are ctx_input_fields
        pipeline = Pipeline([stage])
        assert pipeline.stage_names == ["first"]

    def test_chained_dependencies(self):
        """三阶段链式依赖：A→B→C"""
        a = DummyStage("A", required_inputs=frozenset(), provided_outputs=frozenset({"alpha"}))
        b = DummyStage("B", required_inputs=frozenset({"alpha"}), provided_outputs=frozenset({"beta"}))
        c = DummyStage("C", required_inputs=frozenset({"beta"}), provided_outputs=frozenset())
        # Should not raise
        pipeline = Pipeline([a, b, c])
        assert pipeline.stage_names == ["A", "B", "C"]

    def test_multiple_providers_merge(self):
        """多个 stage 的 provided_outputs 合并后满足下游"""
        a = DummyStage("A", required_inputs=frozenset(), provided_outputs=frozenset({"x"}))
        b = DummyStage("B", required_inputs=frozenset(), provided_outputs=frozenset({"y"}))
        c = DummyStage("C", required_inputs=frozenset({"x", "y"}), provided_outputs=frozenset())
        # Should not raise
        pipeline = Pipeline([a, b, c])
        assert pipeline.stage_names == ["A", "B", "C"]


# ============================================================
# 3. Pipeline raises error if required inputs not satisfied
# ============================================================

class TestPipelineUnsatisfiedInputs:
    """Pipeline 构造/添加 stage 时 unsatisfied inputs 报错"""

    def test_missing_data_dependency_raises(self):
        """stage 需要的字段未被先前 stage 提供 → ValueError"""
        a = DummyStage("A", required_inputs=frozenset(), provided_outputs=frozenset({"x"}))
        b = DummyStage("B", required_inputs=frozenset({"y"}), provided_outputs=frozenset())
        with pytest.raises(ValueError, match="unsatisfied input dependencies"):
            Pipeline([a, b])

    def test_wrong_order_raises(self):
        """stage 顺序错误（消费者在提供者之前）→ ValueError"""
        consumer = DummyStage("consumer", required_inputs=frozenset({"data"}), provided_outputs=frozenset())
        provider = DummyStage("provider", required_inputs=frozenset(), provided_outputs=frozenset({"data"}))
        with pytest.raises(ValueError, match="unsatisfied input dependencies"):
            Pipeline([consumer, provider])

    def test_add_stage_detects_unsatisfied(self):
        """add_stage 也能检测 unsatisfied inputs"""
        pipeline = Pipeline()
        pipeline.add_stage(DummyStage("A", provided_outputs=frozenset({"x"})))
        with pytest.raises(ValueError, match="unsatisfied input dependencies"):
            pipeline.add_stage(DummyStage("B", required_inputs=frozenset({"z"})))

    def test_no_inputs_no_outputs_stage_is_valid(self):
        """无依赖无产出的 stage 始终合法"""
        stage = DummyStage("noop")
        pipeline = Pipeline([stage])
        assert pipeline.stage_names == ["noop"]

    def test_error_message_contains_field_names(self):
        """错误信息包含缺失的字段名"""
        stage = DummyStage("bad", required_inputs=frozenset({"missing_field"}))
        with pytest.raises(ValueError, match="missing_field"):
            Pipeline([stage])

    def test_error_message_contains_available_fields(self):
        """错误信息包含当前可用的字段列表"""
        a = DummyStage("A", provided_outputs=frozenset({"x"}))
        b = DummyStage("B", required_inputs=frozenset({"z"}))
        with pytest.raises(ValueError, match="Available from prior stages"):
            Pipeline([a, b])

    def test_old_style_requires_still_works(self):
        """旧式 requires（按 stage name）仍然生效"""
        class OldStyleStage(PipelineStage):
            requires = {"preprocess"}  # old-style

            @property
            def name(self):
                return "old_style"

            def execute(self, ctx):
                return ctx

        # preprocess not seen before old_style → should raise
        with pytest.raises(ValueError, match="requires.*preprocess"):
            Pipeline([OldStyleStage()])


# ============================================================
# 4. validate_inputs works for each stage
# ============================================================

class TestValidateInputs:
    """PipelineStage.validate_inputs() 运行时校验"""

    def test_validate_inputs_passes_with_valid_ctx(self):
        """所有 required_inputs 字段非空 → 不报错"""
        stage = DummyStage("test", required_inputs=frozenset({"title", "content_type"}))
        ctx = PipelineContext(title="My Note", content_type="lecture")
        # Should not raise
        stage.validate_inputs(ctx)

    def test_validate_inputs_fails_with_empty_string(self):
        """required_inputs 字段为空字符串 → ValueError"""
        stage = DummyStage("test", required_inputs=frozenset({"title"}))
        ctx = PipelineContext(title="")
        with pytest.raises(ValueError, match="missing required inputs.*title"):
            stage.validate_inputs(ctx)

    def test_validate_inputs_fails_with_none(self):
        """required_inputs 字段为 None → ValueError"""
        stage = DummyStage("test", required_inputs=frozenset({"quality_report"}))
        ctx = PipelineContext()  # quality_report defaults to None
        with pytest.raises(ValueError, match="missing required inputs.*quality_report"):
            stage.validate_inputs(ctx)

    def test_validate_inputs_fails_with_empty_list(self):
        """required_inputs 字段为空列表 → ValueError"""
        stage = DummyStage("test", required_inputs=frozenset({"chunks"}))
        ctx = PipelineContext()  # chunks defaults to []
        with pytest.raises(ValueError, match="missing required inputs.*chunks"):
            stage.validate_inputs(ctx)

    def test_validate_inputs_fails_with_zero_number(self):
        """required_inputs 数值字段为 0 → ValueError"""
        stage = DummyStage("test", required_inputs=frozenset({"total_score"}))
        ctx = PipelineContext()  # total_score defaults to 0.0
        with pytest.raises(ValueError, match="missing required inputs.*total_score"):
            stage.validate_inputs(ctx)

    def test_validate_inputs_bool_always_valid(self):
        """布尔字段始终有效（False 是合法值）"""
        stage = DummyStage("test", required_inputs=frozenset({"overall_passed"}))
        ctx = PipelineContext(overall_passed=False)
        # Should not raise — False is a valid bool
        stage.validate_inputs(ctx)

    def test_validate_inputs_with_nonexistent_field(self):
        """required_inputs 引用不存在的字段 → 视为 None → ValueError"""
        stage = DummyStage("test", required_inputs=frozenset({"nonexistent_field"}))
        ctx = PipelineContext()
        with pytest.raises(ValueError, match="missing required inputs.*nonexistent_field"):
            stage.validate_inputs(ctx)

    def test_validate_inputs_multiple_missing(self):
        """多个 required_inputs 缺失 → 错误信息列出所有缺失字段"""
        stage = DummyStage("test", required_inputs=frozenset({"title", "clean_text", "chunks"}))
        ctx = PipelineContext()
        with pytest.raises(ValueError, match="title") as exc_info:
            stage.validate_inputs(ctx)
        # All missing fields should be mentioned
        error_msg = str(exc_info.value)
        assert "title" in error_msg
        assert "clean_text" in error_msg
        assert "chunks" in error_msg

    def test_validate_inputs_no_required_inputs(self):
        """无 required_inputs → 始终通过"""
        stage = DummyStage("test", required_inputs=frozenset())
        ctx = PipelineContext()
        # Should not raise
        stage.validate_inputs(ctx)

    # --- Per-stage validate_inputs tests ---

    def test_preprocess_validate_inputs_with_valid_ctx(self):
        from noteforge.engine.stages.preprocess import PreprocessStage
        stage = PreprocessStage(MagicMock(), {})
        ctx = PipelineContext(source_path="ep01", transcript_path="output/transcripts/ep01.txt")
        stage.validate_inputs(ctx)  # should not raise

    def test_preprocess_validate_inputs_with_empty_source(self):
        from noteforge.engine.stages.preprocess import PreprocessStage
        stage = PreprocessStage(MagicMock(), {})
        ctx = PipelineContext(source_path="", transcript_path="output/transcripts/ep01.txt")
        with pytest.raises(ValueError, match="source_path"):
            stage.validate_inputs(ctx)

    def test_generate_validate_inputs_with_valid_ctx(self):
        from noteforge.engine.stages.generate import GenerateStage
        stage = GenerateStage(MagicMock(), MagicMock(), MagicMock())
        ctx = PipelineContext(
            clean_text="some text",
            chunks=["some text"],
            title="Test",
            content_type="lecture",
        )
        stage.validate_inputs(ctx)  # should not raise

    def test_generate_validate_inputs_with_empty_chunks(self):
        from noteforge.engine.stages.generate import GenerateStage
        stage = GenerateStage(MagicMock(), MagicMock(), MagicMock())
        ctx = PipelineContext(
            clean_text="some text",
            chunks=[],
            title="Test",
            content_type="lecture",
        )
        with pytest.raises(ValueError, match="chunks"):
            stage.validate_inputs(ctx)

    def test_format_validate_inputs_with_valid_ctx(self):
        from noteforge.engine.stages.format import FormatStage
        stage = FormatStage(MagicMock())
        ctx = PipelineContext(note_text="some note", content_type="lecture")
        stage.validate_inputs(ctx)  # should not raise

    def test_format_validate_inputs_with_empty_note(self):
        from noteforge.engine.stages.format import FormatStage
        stage = FormatStage(MagicMock())
        ctx = PipelineContext(note_text="", content_type="lecture")
        with pytest.raises(ValueError, match="note_text"):
            stage.validate_inputs(ctx)

    def test_quality_gate_validate_inputs_with_valid_ctx(self):
        from noteforge.engine.stages.evaluate import QualityGateStage
        stage = QualityGateStage(MagicMock(), reports_dir=Path("/tmp"))
        ctx = PipelineContext(formatted_text="formatted", clean_text="clean")
        stage.validate_inputs(ctx)  # should not raise

    def test_save_validate_inputs_with_valid_ctx(self):
        from noteforge.engine.stages.save import SaveStage
        stage = SaveStage(notes_dir=Path("/tmp"))
        ctx = PipelineContext(formatted_text="formatted", output_path="/tmp/test.md", title="Test")
        stage.validate_inputs(ctx)  # should not raise

    def test_postprocess_validate_inputs_with_valid_ctx(self):
        from noteforge.engine.stages.postprocess import PostProcessStage
        stage = PostProcessStage()
        ctx = PipelineContext(note_text="note", output_path="/tmp/test.md")
        stage.validate_inputs(ctx)  # should not raise


# ============================================================
# 5. Backward compatibility
# ============================================================

class TestBackwardCompatibility:
    """确保新增协议不破坏现有行为"""

    def test_pipeline_with_no_declarations_still_works(self):
        """未声明 required_inputs/provided_outputs 的 stage 仍可正常编排"""
        class LegacyStage(PipelineStage):
            @property
            def name(self):
                return "legacy"

            def execute(self, ctx):
                ctx.note_text = "generated"
                return ctx

        pipeline = Pipeline([LegacyStage()])
        ctx = PipelineContext()
        result = pipeline.run(ctx)
        assert result.note_text == "generated"
        assert result.error is None

    def test_pipeline_run_does_not_call_validate_inputs(self):
        """Pipeline.run() 不自动调用 validate_inputs（保持现有行为）"""
        stage = DummyStage("test", required_inputs=frozenset({"title"}))
        pipeline = Pipeline([stage])
        ctx = PipelineContext(title="")  # empty title
        # run should not raise — validate_inputs is opt-in
        result = pipeline.run(ctx)
        assert result.error is None

    def test_existing_tests_still_pass_context_defaults(self):
        """PipelineContext 默认值不变"""
        ctx = PipelineContext()
        assert ctx.content_type == "lecture"
        assert ctx.mode == "notes"
        assert ctx.error is None
        assert ctx.chunks == []
        assert ctx.token_usage == {}
        assert ctx.raw_text == ""
        assert ctx.clean_text == ""
        assert ctx.note_text == ""
        assert ctx.formatted_text == ""
        assert ctx.total_score == 0.0
        assert ctx.overall_passed is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
