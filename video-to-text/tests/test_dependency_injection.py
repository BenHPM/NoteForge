# -*- coding: utf-8 -*-
"""
测试 LLMNoteEngine 依赖注入

覆盖:
  - 注入 mock provider，验证引擎使用注入实例
  - 注入 mock quality_manager，验证引擎使用注入实例
  - 注入所有子系统，验证全部使用注入实例
  - 不注入任何子系统，验证默认行为不变
  - 注入的子系统在引擎方法中被实际调用

运行:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_dependency_injection.py -v
"""

import os
import tempfile
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from noteforge.engine.note_engine import LLMNoteEngine
from noteforge.core.llm_providers import LLMProvider
from noteforge.core.transcript_preprocessor import TranscriptPreprocessor
from noteforge.core.prompt_builder import PromptBuilder
from noteforge.core.note_formatter import NoteFormatter
from noteforge.core.token_manager import TokenManager
from noteforge.core.domain_classifier import DomainClassifier
from noteforge.core.audio_handler import AudioHandler
from noteforge.quality.manager import QualityManager
from noteforge.intelligence.synthesis import SynthesisEngine


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_yaml_dir():
    """创建包含最小 YAML 配置的临时目录"""
    tmp = tempfile.mkdtemp()
    yaml_content = """
provider:
  type: "claude"
  claude:
    model: "claude-sonnet-4-20250514"
    base_url: "http://127.0.0.1:15721"
    max_tokens: 8192
    temperature: 0.3

quality:
  min_score: 0.80
  max_retries: 2
  retry_temperature_delta: 0.1
"""
    yaml_path = os.path.join(tmp, "llm_engine_config.yaml")
    with open(yaml_path, 'w', encoding='utf-8') as f:
        f.write(yaml_content)
    # 创建必要的输出子目录，避免 mkdir 报错
    for sub in ('notes', 'quality_reports', 'logs', 'transcripts', 'audio'):
        os.makedirs(os.path.join(tmp, 'output', sub), exist_ok=True)
    yield tmp
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def config_path(sample_yaml_dir):
    return os.path.join(sample_yaml_dir, "llm_engine_config.yaml")


@pytest.fixture
def mock_provider():
    """创建 mock LLMProvider"""
    p = MagicMock(spec=LLMProvider)
    p.get_name.return_value = "mock-provider"
    p.get_usage.return_value = {'input_tokens': 0, 'output_tokens': 0}
    return p


@pytest.fixture
def mock_preprocessor():
    return MagicMock(spec=TranscriptPreprocessor)


@pytest.fixture
def mock_prompt_builder():
    return MagicMock(spec=PromptBuilder)


@pytest.fixture
def mock_formatter():
    return MagicMock(spec=NoteFormatter)


@pytest.fixture
def mock_quality_manager():
    qm = MagicMock(spec=QualityManager)
    qm._content_type = None
    return qm


@pytest.fixture
def mock_domain_classifier():
    dc = MagicMock(spec=DomainClassifier)
    dc.detect_domain.return_value = "general"
    dc.get_domain_config.return_value = {}
    dc.get_notes_by_domain.return_value = {}
    return dc


@pytest.fixture
def mock_token_manager():
    return MagicMock(spec=TokenManager)


@pytest.fixture
def mock_audio_handler():
    ah = MagicMock(spec=AudioHandler)
    ah.extract_title.return_value = "Test Title"
    return ah


@pytest.fixture
def mock_synthesis_engine():
    return MagicMock(spec=SynthesisEngine)


# ============================================================
# Tests: Injected mock provider
# ============================================================

class TestInjectedProvider:
    """注入 mock provider，验证引擎使用注入实例"""

    def test_injected_provider_stored(self, config_path, mock_provider):
        """注入的 provider 应直接存储到 _provider"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                provider=mock_provider,
            )
        assert engine._provider is mock_provider

    def test_injected_provider_returned_by_get_provider(self, config_path, mock_provider):
        """_get_provider() 应返回注入的 provider，不再创建新的"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                provider=mock_provider,
            )
        result = engine._get_provider()
        assert result is mock_provider

    def test_injected_provider_not_overridden(self, config_path, mock_provider):
        """多次调用 _get_provider() 不应覆盖注入的 provider"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                provider=mock_provider,
            )
        p1 = engine._get_provider()
        p2 = engine._get_provider()
        assert p1 is mock_provider
        assert p2 is mock_provider


# ============================================================
# Tests: Injected mock quality_manager
# ============================================================

class TestInjectedQualityManager:
    """注入 mock quality_manager，验证引擎使用注入实例"""

    def test_injected_quality_manager_stored(self, config_path, mock_quality_manager):
        """注入的 quality_manager 应直接存储到 self.quality_manager"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                quality_manager=mock_quality_manager,
            )
        assert engine.quality_manager is mock_quality_manager

    def test_injected_quality_manager_not_replaced(self, config_path, mock_quality_manager):
        """注入的 quality_manager 不应被内部创建的实例替换"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                quality_manager=mock_quality_manager,
            )
        # quality_manager 应该仍然是注入的 mock
        assert isinstance(engine.quality_manager, MagicMock)


# ============================================================
# Tests: All subsystems injected
# ============================================================

class TestAllSubsystemsInjected:
    """注入所有子系统，验证全部使用注入实例"""

    def test_all_injected_subsystems_used(self, config_path, mock_provider,
                                           mock_preprocessor, mock_prompt_builder,
                                           mock_formatter, mock_quality_manager,
                                           mock_domain_classifier, mock_token_manager,
                                           mock_audio_handler, mock_synthesis_engine):
        """所有注入的子系统应被引擎直接使用"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                provider=mock_provider,
                preprocessor=mock_preprocessor,
                prompt_builder=mock_prompt_builder,
                formatter=mock_formatter,
                quality_manager=mock_quality_manager,
                domain_classifier=mock_domain_classifier,
                token_manager=mock_token_manager,
                audio_handler=mock_audio_handler,
                synthesis_engine=mock_synthesis_engine,
            )

        assert engine._provider is mock_provider
        assert engine.preprocessor is mock_preprocessor
        assert engine._prompt_builder is mock_prompt_builder
        assert engine.formatter is mock_formatter
        assert engine.quality_manager is mock_quality_manager
        assert engine._domain_classifier is mock_domain_classifier
        assert engine.token_manager is mock_token_manager
        assert engine._audio_handler is mock_audio_handler
        assert engine._synthesis_engine is mock_synthesis_engine

    def test_injected_prompt_builder_not_rebuilt(self, config_path, mock_prompt_builder):
        """注入的 prompt_builder 不应被 _get_prompt_builder 重建"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                prompt_builder=mock_prompt_builder,
            )
        result = engine._get_prompt_builder()
        assert result is mock_prompt_builder


# ============================================================
# Tests: No injections (default behavior unchanged)
# ============================================================

class TestNoInjections:
    """不注入任何子系统，验证默认行为不变"""

    def test_default_preprocessor_type(self, config_path):
        """默认创建 TranscriptPreprocessor 实例"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(config_path=config_path)
        assert isinstance(engine.preprocessor, TranscriptPreprocessor)

    def test_default_formatter_type(self, config_path):
        """默认创建 NoteFormatter 实例"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(config_path=config_path)
        assert isinstance(engine.formatter, NoteFormatter)

    def test_default_token_manager_type(self, config_path):
        """默认创建 TokenManager 实例"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(config_path=config_path)
        assert isinstance(engine.token_manager, TokenManager)

    def test_default_domain_classifier_type(self, config_path):
        """默认创建 DomainClassifier 实例"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(config_path=config_path)
        assert isinstance(engine._domain_classifier, DomainClassifier)

    def test_default_audio_handler_type(self, config_path):
        """默认创建 AudioHandler 实例"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(config_path=config_path)
        assert isinstance(engine._audio_handler, AudioHandler)

    def test_default_quality_manager_type(self, config_path):
        """默认创建 QualityManager 实例"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(config_path=config_path)
        assert isinstance(engine.quality_manager, QualityManager)

    def test_default_synthesis_engine_type(self, config_path):
        """默认创建 SynthesisEngine 实例"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(config_path=config_path)
        assert isinstance(engine._synthesis_engine, SynthesisEngine)

    def test_default_provider_is_none(self, config_path):
        """默认 _provider 为 None（延迟初始化）"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(config_path=config_path)
        assert engine._provider is None

    def test_default_prompt_builder_is_none(self, config_path):
        """默认 _prompt_builder 为 None（延迟初始化）"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(config_path=config_path)
        assert engine._prompt_builder is None

    def test_default_prompt_builder_lazy_init(self, config_path):
        """默认 _get_prompt_builder 延迟创建 PromptBuilder"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(config_path=config_path)
        pb = engine._get_prompt_builder()
        assert isinstance(pb, PromptBuilder)
        # 再次调用应返回同一实例
        assert engine._get_prompt_builder() is pb


# ============================================================
# Tests: Injected subsystems are actually used
# ============================================================

class TestInjectedSubsystemsActuallyUsed:
    """验证注入的子系统在引擎方法中被实际调用"""

    def test_injected_domain_classifier_used_by_detect_domain(
        self, config_path, mock_domain_classifier
    ):
        """detect_domain 应委托给注入的 domain_classifier"""
        mock_domain_classifier.detect_domain.return_value = "finance_investment"
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                domain_classifier=mock_domain_classifier,
            )
        result = engine.detect_domain("some_note.md")
        assert result == "finance_investment"
        mock_domain_classifier.detect_domain.assert_called_once_with("some_note.md")

    def test_injected_domain_classifier_used_by_get_domain_config(
        self, config_path, mock_domain_classifier
    ):
        """get_domain_config 应委托给注入的 domain_classifier"""
        mock_domain_classifier.get_domain_config.return_value = {"keywords": ["test"]}
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                domain_classifier=mock_domain_classifier,
            )
        result = engine.get_domain_config("finance_investment")
        assert result == {"keywords": ["test"]}
        mock_domain_classifier.get_domain_config.assert_called_once_with("finance_investment")

    def test_injected_domain_classifier_used_by_get_notes_by_domain(
        self, config_path, mock_domain_classifier
    ):
        """get_notes_by_domain 应委托给注入的 domain_classifier"""
        mock_domain_classifier.get_notes_by_domain.return_value = {"finance": ["a.md"]}
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                domain_classifier=mock_domain_classifier,
            )
        result = engine.get_notes_by_domain()
        assert result == {"finance": ["a.md"]}
        mock_domain_classifier.get_notes_by_domain.assert_called_once()

    def test_injected_domain_classifier_used_by_validate_domain_match(
        self, config_path, mock_domain_classifier
    ):
        """validate_domain_match 应委托给注入的 domain_classifier"""
        mock_domain_classifier.validate_domain_match.return_value = (True, "same domain")
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                domain_classifier=mock_domain_classifier,
            )
        result = engine.validate_domain_match("note.md", "synthesis.md")
        assert result == (True, "same domain")
        mock_domain_classifier.validate_domain_match.assert_called_once_with(
            "note.md", "synthesis.md"
        )

    def test_injected_synthesis_engine_used_by_generate_synthesis(
        self, config_path, mock_synthesis_engine, mock_provider
    ):
        """generate_synthesis 应委托给注入的 synthesis_engine"""
        mock_synthesis_engine.generate_synthesis.return_value = "/path/to/synthesis.md"
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                provider=mock_provider,
                synthesis_engine=mock_synthesis_engine,
            )
        result = engine.generate_synthesis(domain="test_domain")
        assert result == "/path/to/synthesis.md"
        mock_synthesis_engine.generate_synthesis.assert_called_once()

    def test_injected_synthesis_engine_used_by_two_stage(
        self, config_path, mock_synthesis_engine, mock_provider
    ):
        """generate_synthesis_two_stage 应委托给注入的 synthesis_engine"""
        mock_synthesis_engine.generate_synthesis_two_stage.return_value = "/path/to/synthesis.md"
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                provider=mock_provider,
                synthesis_engine=mock_synthesis_engine,
            )
        result = engine.generate_synthesis_two_stage(domain="test_domain")
        assert result == "/path/to/synthesis.md"
        mock_synthesis_engine.generate_synthesis_two_stage.assert_called_once()

    def test_injected_synthesis_engine_used_by_incremental(
        self, config_path, mock_synthesis_engine, mock_provider
    ):
        """update_synthesis_incremental 应委托给注入的 synthesis_engine"""
        mock_synthesis_engine.update_synthesis_incremental.return_value = "/path/to/synthesis.md"
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                provider=mock_provider,
                synthesis_engine=mock_synthesis_engine,
            )
        result = engine.update_synthesis_incremental(new_note_path="note.md")
        assert result == "/path/to/synthesis.md"
        mock_synthesis_engine.update_synthesis_incremental.assert_called_once()

    def test_injected_audio_handler_used_by_resolve_inputs(
        self, config_path, mock_audio_handler
    ):
        """_resolve_inputs 应使用注入的 audio_handler 提取标题"""
        mock_audio_handler.extract_title.return_value = "Extracted Title"
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                audio_handler=mock_audio_handler,
            )
        # 创建一个临时转写文件
        tmp = tempfile.NamedTemporaryFile(suffix='.txt', delete=False, dir=config_path.replace('llm_engine_config.yaml', 'output/transcripts'))
        tmp.write(b"test content")
        tmp.close()
        try:
            result = engine._resolve_inputs(tmp.name)
            mock_audio_handler.extract_title.assert_called_once()
            assert result is not None
            assert result.get('title') == "Extracted Title"
        finally:
            os.unlink(tmp.name)

    def test_injected_quality_manager_used_by_check_only(
        self, config_path, mock_quality_manager, mock_audio_handler
    ):
        """check_only 应委托给注入的 quality_manager"""
        mock_audio_handler.find_transcript_for_note.return_value = "/path/to/transcript.txt"
        mock_quality_manager.check_only.return_value = {"score": 0.9}
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                quality_manager=mock_quality_manager,
                audio_handler=mock_audio_handler,
            )
        result = engine.check_only("note.md")
        assert result == {"score": 0.9}
        mock_quality_manager.check_only.assert_called_once()

    def test_injected_token_manager_used_by_track_tokens(
        self, config_path, mock_token_manager, mock_provider
    ):
        """_track_tokens 应使用注入的 token_manager 记录使用量"""
        mock_provider.get_usage.return_value = {
            'input_tokens': 100, 'output_tokens': 50
        }
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                provider=mock_provider,
                token_manager=mock_token_manager,
            )
        engine._track_tokens(mock_provider, purpose="test")
        mock_token_manager.record.assert_called_once()

    def test_injected_preprocessor_used_in_pipeline(
        self, config_path, mock_preprocessor, mock_provider,
        mock_prompt_builder, mock_formatter, mock_quality_manager,
    ):
        """generate_note 的 Pipeline 应使用注入的 preprocessor"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                provider=mock_provider,
                preprocessor=mock_preprocessor,
                prompt_builder=mock_prompt_builder,
                formatter=mock_formatter,
                quality_manager=mock_quality_manager,
            )
        # 验证 preprocessor 是注入的实例（Pipeline 的 PreprocessStage 会使用它）
        assert engine.preprocessor is mock_preprocessor


# ============================================================
# Tests: Partial injection
# ============================================================

class TestPartialInjection:
    """部分注入子系统，其余使用默认创建"""

    def test_inject_provider_others_default(self, config_path, mock_provider):
        """只注入 provider，其余子系统使用默认创建"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                provider=mock_provider,
            )
        assert engine._provider is mock_provider
        assert isinstance(engine.preprocessor, TranscriptPreprocessor)
        assert isinstance(engine.formatter, NoteFormatter)
        assert isinstance(engine.token_manager, TokenManager)
        assert isinstance(engine.quality_manager, QualityManager)

    def test_inject_quality_manager_others_default(self, config_path, mock_quality_manager):
        """只注入 quality_manager，其余子系统使用默认创建"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                quality_manager=mock_quality_manager,
            )
        assert engine.quality_manager is mock_quality_manager
        assert isinstance(engine.preprocessor, TranscriptPreprocessor)
        assert engine._provider is None  # 延迟初始化

    def test_inject_preprocessor_and_formatter(self, config_path,
                                                 mock_preprocessor, mock_formatter):
        """注入 preprocessor 和 formatter，其余默认"""
        with patch('noteforge.infra.env.check_env'):
            engine = LLMNoteEngine(
                config_path=config_path,
                preprocessor=mock_preprocessor,
                formatter=mock_formatter,
            )
        assert engine.preprocessor is mock_preprocessor
        assert engine.formatter is mock_formatter
        assert isinstance(engine.token_manager, TokenManager)
        assert isinstance(engine.quality_manager, QualityManager)
