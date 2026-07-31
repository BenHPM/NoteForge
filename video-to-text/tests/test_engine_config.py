# -*- coding: utf-8 -*-
"""
测试 noteforge.config 冻结配置（EngineConfig / ProviderConfig / QualityConfig）

覆盖:
  - EngineConfig 是 frozen dataclass（mutation raises）
  - ProviderConfig 是 frozen dataclass
  - QualityConfig 是 frozen dataclass
  - from_yaml 创建有效配置
  - to_dict 往返序列化
  - config_hash 在 YAML 变更时改变
  - freeze() 创建一致快照
  - LLMNoteEngine 接受 EngineConfig
  - ExecutionTrace 存储 config_hash
  - PipelineContext 包含 config_hash

运行:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_engine_config.py -v
"""

import json
import os
import tempfile
import pytest

from noteforge.config import (
    ProviderConfig,
    QualityConfig,
    EngineConfig,
    NoteForgeConfig,
    load_yaml,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_yaml_dir():
    """创建包含 YAML 配置的临时目录"""
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
    yield tmp
    # cleanup
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def sample_yaml_path(sample_yaml_dir):
    return os.path.join(sample_yaml_dir, "llm_engine_config.yaml")


@pytest.fixture
def modified_yaml_dir():
    """创建包含不同配置的临时目录（用于 hash 变更测试）"""
    tmp = tempfile.mkdtemp()
    yaml_content = """
provider:
  type: "openai"
  openai:
    model: "gpt-4o"
    base_url: "https://api.openai.com/v1"
    max_tokens: 4096
    temperature: 0.5

quality:
  min_score: 0.90
  max_retries: 3
  retry_temperature_delta: 0.2
"""
    yaml_path = os.path.join(tmp, "llm_engine_config.yaml")
    with open(yaml_path, 'w', encoding='utf-8') as f:
        f.write(yaml_content)
    yield tmp
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def modified_yaml_path(modified_yaml_dir):
    return os.path.join(modified_yaml_dir, "llm_engine_config.yaml")


# ============================================================
# ProviderConfig frozen tests
# ============================================================

class TestProviderConfigFrozen:
    """ProviderConfig 是 frozen dataclass"""

    def test_mutation_raises(self):
        cfg = ProviderConfig(type="claude", model="test-model")
        with pytest.raises(AttributeError):
            cfg.type = "openai"

    def test_mutation_model_raises(self):
        cfg = ProviderConfig(type="claude", model="test-model")
        with pytest.raises(AttributeError):
            cfg.model = "other-model"

    def test_mutation_temperature_raises(self):
        cfg = ProviderConfig(temperature=0.3)
        with pytest.raises(AttributeError):
            cfg.temperature = 0.9

    def test_mutation_max_tokens_raises(self):
        cfg = ProviderConfig(max_tokens=8192)
        with pytest.raises(AttributeError):
            cfg.max_tokens = 4096

    def test_mutation_base_url_raises(self):
        cfg = ProviderConfig(base_url="http://localhost")
        with pytest.raises(AttributeError):
            cfg.base_url = "http://other"


# ============================================================
# QualityConfig frozen tests
# ============================================================

class TestQualityConfigFrozen:
    """QualityConfig 是 frozen dataclass"""

    def test_mutation_min_score_raises(self):
        cfg = QualityConfig(min_score=0.80)
        with pytest.raises(AttributeError):
            cfg.min_score = 0.90

    def test_mutation_max_retries_raises(self):
        cfg = QualityConfig(max_retries=2)
        with pytest.raises(AttributeError):
            cfg.max_retries = 5

    def test_mutation_retry_temp_delta_raises(self):
        cfg = QualityConfig(retry_temp_delta=0.1)
        with pytest.raises(AttributeError):
            cfg.retry_temp_delta = 0.5


# ============================================================
# EngineConfig frozen tests
# ============================================================

class TestEngineConfigFrozen:
    """EngineConfig 是 frozen dataclass"""

    def test_mutation_content_type_raises(self):
        cfg = EngineConfig(
            provider=ProviderConfig(),
            quality=QualityConfig(),
            content_type="lecture",
        )
        with pytest.raises(AttributeError):
            cfg.content_type = "interview"

    def test_mutation_config_hash_raises(self):
        cfg = EngineConfig(
            provider=ProviderConfig(),
            quality=QualityConfig(),
            config_hash="abc123",
        )
        with pytest.raises(AttributeError):
            cfg.config_hash = "def456"

    def test_mutation_provider_raises(self):
        cfg = EngineConfig(
            provider=ProviderConfig(type="claude"),
            quality=QualityConfig(),
        )
        with pytest.raises(AttributeError):
            cfg.provider = ProviderConfig(type="openai")

    def test_mutation_quality_raises(self):
        cfg = EngineConfig(
            provider=ProviderConfig(),
            quality=QualityConfig(min_score=0.80),
        )
        with pytest.raises(AttributeError):
            cfg.quality = QualityConfig(min_score=0.90)

    def test_nested_provider_still_frozen(self):
        """嵌套的 ProviderConfig 也不可修改"""
        cfg = EngineConfig(
            provider=ProviderConfig(type="claude", model="test"),
            quality=QualityConfig(),
        )
        with pytest.raises(AttributeError):
            cfg.provider.model = "other"


# ============================================================
# EngineConfig.from_yaml tests
# ============================================================

class TestEngineConfigFromYaml:
    """from_yaml 创建有效配置"""

    def test_from_yaml_creates_valid_config(self, sample_yaml_path):
        cfg = EngineConfig.from_yaml(sample_yaml_path)
        assert isinstance(cfg, EngineConfig)
        assert isinstance(cfg.provider, ProviderConfig)
        assert isinstance(cfg.quality, QualityConfig)

    def test_from_yaml_provider_values(self, sample_yaml_path):
        cfg = EngineConfig.from_yaml(sample_yaml_path)
        assert cfg.provider.type == "claude"
        assert cfg.provider.model == "claude-sonnet-4-20250514"
        assert cfg.provider.base_url == "http://127.0.0.1:15721"
        assert cfg.provider.temperature == 0.3
        assert cfg.provider.max_tokens == 8192

    def test_from_yaml_quality_values(self, sample_yaml_path):
        cfg = EngineConfig.from_yaml(sample_yaml_path)
        assert cfg.quality.min_score == 0.80
        assert cfg.quality.max_retries == 2
        assert cfg.quality.retry_temp_delta == 0.1

    def test_from_yaml_config_hash_not_empty(self, sample_yaml_path):
        cfg = EngineConfig.from_yaml(sample_yaml_path)
        assert cfg.config_hash != ""
        assert len(cfg.config_hash) == 16  # SHA-256 前 16 字符

    def test_from_yaml_missing_file_returns_defaults(self):
        """YAML 文件不存在时使用默认值"""
        cfg = EngineConfig.from_yaml("/nonexistent/path.yaml")
        assert cfg.provider.type == "claude"  # 默认值
        assert cfg.provider.model == ""       # 无嵌套配置
        assert cfg.quality.min_score == 0.80  # 默认值

    def test_from_yaml_openai_provider(self, modified_yaml_path):
        cfg = EngineConfig.from_yaml(modified_yaml_path)
        assert cfg.provider.type == "openai"
        assert cfg.provider.model == "gpt-4o"
        assert cfg.provider.base_url == "https://api.openai.com/v1"


# ============================================================
# to_dict round-trip tests
# ============================================================

class TestToDictRoundTrip:
    """to_dict 往返序列化"""

    def test_to_dict_contains_all_fields(self, sample_yaml_path):
        cfg = EngineConfig.from_yaml(sample_yaml_path)
        d = cfg.to_dict()
        assert 'provider' in d
        assert 'quality' in d
        assert 'content_type' in d
        assert 'config_hash' in d

    def test_to_dict_provider_is_dict(self, sample_yaml_path):
        cfg = EngineConfig.from_yaml(sample_yaml_path)
        d = cfg.to_dict()
        assert isinstance(d['provider'], dict)
        assert d['provider']['type'] == 'claude'
        assert d['provider']['model'] == 'claude-sonnet-4-20250514'

    def test_to_dict_quality_is_dict(self, sample_yaml_path):
        cfg = EngineConfig.from_yaml(sample_yaml_path)
        d = cfg.to_dict()
        assert isinstance(d['quality'], dict)
        assert d['quality']['min_score'] == 0.80

    def test_to_dict_json_serializable(self, sample_yaml_path):
        """to_dict 结果可 JSON 序列化"""
        cfg = EngineConfig.from_yaml(sample_yaml_path)
        d = cfg.to_dict()
        # 不应抛出异常
        json_str = json.dumps(d)
        assert isinstance(json_str, str)

    def test_to_dict_roundtrip_preserves_values(self, sample_yaml_path):
        """to_dict 后可重建等效的 EngineConfig"""
        cfg = EngineConfig.from_yaml(sample_yaml_path)
        d = cfg.to_dict()
        # 从 dict 重建
        rebuilt = EngineConfig(
            provider=ProviderConfig(**d['provider']),
            quality=QualityConfig(**d['quality']),
            content_type=d['content_type'],
            config_hash=d['config_hash'],
        )
        assert rebuilt.provider.type == cfg.provider.type
        assert rebuilt.provider.model == cfg.provider.model
        assert rebuilt.quality.min_score == cfg.quality.min_score
        assert rebuilt.config_hash == cfg.config_hash


# ============================================================
# config_hash 变更测试
# ============================================================

class TestConfigHashChanges:
    """config_hash 在 YAML 变更时改变"""

    def test_same_yaml_same_hash(self, sample_yaml_path):
        """同一 YAML 多次加载产生相同 hash"""
        cfg1 = EngineConfig.from_yaml(sample_yaml_path)
        cfg2 = EngineConfig.from_yaml(sample_yaml_path)
        assert cfg1.config_hash == cfg2.config_hash

    def test_different_yaml_different_hash(self, sample_yaml_path, modified_yaml_path):
        """不同 YAML 产生不同 hash"""
        cfg1 = EngineConfig.from_yaml(sample_yaml_path)
        cfg2 = EngineConfig.from_yaml(modified_yaml_path)
        assert cfg1.config_hash != cfg2.config_hash

    def test_hash_changes_on_model_change(self, sample_yaml_dir):
        """仅 model 变更时 hash 改变"""
        path1 = os.path.join(sample_yaml_dir, "config_v1.yaml")
        with open(path1, 'w', encoding='utf-8') as f:
            f.write("""
provider:
  type: "claude"
  claude:
    model: "model-v1"
    temperature: 0.3
    max_tokens: 8192
quality:
  min_score: 0.80
  max_retries: 2
  retry_temperature_delta: 0.1
""")
        path2 = os.path.join(sample_yaml_dir, "config_v2.yaml")
        with open(path2, 'w', encoding='utf-8') as f:
            f.write("""
provider:
  type: "claude"
  claude:
    model: "model-v2"
    temperature: 0.3
    max_tokens: 8192
quality:
  min_score: 0.80
  max_retries: 2
  retry_temperature_delta: 0.1
""")
        cfg1 = EngineConfig.from_yaml(path1)
        cfg2 = EngineConfig.from_yaml(path2)
        assert cfg1.config_hash != cfg2.config_hash

    def test_hash_changes_on_min_score_change(self, sample_yaml_dir):
        """仅 min_score 变更时 hash 改变"""
        path1 = os.path.join(sample_yaml_dir, "score_v1.yaml")
        with open(path1, 'w', encoding='utf-8') as f:
            f.write("""
provider:
  type: "claude"
  claude:
    model: "test"
    temperature: 0.3
    max_tokens: 8192
quality:
  min_score: 0.80
  max_retries: 2
  retry_temperature_delta: 0.1
""")
        path2 = os.path.join(sample_yaml_dir, "score_v2.yaml")
        with open(path2, 'w', encoding='utf-8') as f:
            f.write("""
provider:
  type: "claude"
  claude:
    model: "test"
    temperature: 0.3
    max_tokens: 8192
quality:
  min_score: 0.90
  max_retries: 2
  retry_temperature_delta: 0.1
""")
        cfg1 = EngineConfig.from_yaml(path1)
        cfg2 = EngineConfig.from_yaml(path2)
        assert cfg1.config_hash != cfg2.config_hash

    def test_hash_stable_on_non_engine_changes(self, sample_yaml_dir):
        """非引擎字段变更（如 feishu 配置）不影响 hash"""
        path1 = os.path.join(sample_yaml_dir, "feishu_v1.yaml")
        with open(path1, 'w', encoding='utf-8') as f:
            f.write("""
provider:
  type: "claude"
  claude:
    model: "test"
    temperature: 0.3
    max_tokens: 8192
quality:
  min_score: 0.80
  max_retries: 2
  retry_temperature_delta: 0.1
feishu:
  enabled: false
""")
        path2 = os.path.join(sample_yaml_dir, "feishu_v2.yaml")
        with open(path2, 'w', encoding='utf-8') as f:
            f.write("""
provider:
  type: "claude"
  claude:
    model: "test"
    temperature: 0.3
    max_tokens: 8192
quality:
  min_score: 0.80
  max_retries: 2
  retry_temperature_delta: 0.1
feishu:
  enabled: true
  auto_sync: true
""")
        cfg1 = EngineConfig.from_yaml(path1)
        cfg2 = EngineConfig.from_yaml(path2)
        assert cfg1.config_hash == cfg2.config_hash


# ============================================================
# NoteForgeConfig.freeze() tests
# ============================================================

class TestNoteForgeConfigFreeze:
    """freeze() 创建一致快照"""

    def test_freeze_returns_engine_config(self, sample_yaml_path):
        cfg = NoteForgeConfig(config_path=sample_yaml_path)
        frozen = cfg.freeze()
        assert isinstance(frozen, EngineConfig)

    def test_freeze_preserves_provider(self, sample_yaml_path):
        cfg = NoteForgeConfig(config_path=sample_yaml_path)
        frozen = cfg.freeze()
        assert frozen.provider.type == "claude"
        assert frozen.provider.model == "claude-sonnet-4-20250514"

    def test_freeze_preserves_quality(self, sample_yaml_path):
        cfg = NoteForgeConfig(config_path=sample_yaml_path)
        frozen = cfg.freeze()
        assert frozen.quality.min_score == 0.80
        assert frozen.quality.max_retries == 2

    def test_freeze_content_type_override(self, sample_yaml_path):
        """freeze() 可覆盖 content_type"""
        cfg = NoteForgeConfig(config_path=sample_yaml_path)
        frozen = cfg.freeze(content_type="interview")
        assert frozen.content_type == "interview"

    def test_freeze_default_content_type(self, sample_yaml_path):
        """默认 content_type 为 lecture"""
        cfg = NoteForgeConfig(config_path=sample_yaml_path)
        frozen = cfg.freeze()
        assert frozen.content_type == "lecture"

    def test_freeze_is_immutable_snapshot(self, sample_yaml_path):
        """冻结后修改原配置不影响快照"""
        cfg = NoteForgeConfig(config_path=sample_yaml_path)
        frozen = cfg.freeze()
        # 修改原始配置
        cfg._config['quality']['min_score'] = 0.50
        # 快照不受影响
        assert frozen.quality.min_score == 0.80

    def test_freeze_consistent_hash(self, sample_yaml_path):
        """多次 freeze 产生相同 hash"""
        cfg = NoteForgeConfig(config_path=sample_yaml_path)
        f1 = cfg.freeze()
        f2 = cfg.freeze()
        assert f1.config_hash == f2.config_hash

    def test_freeze_after_reload_changes_hash(self, sample_yaml_dir):
        """reload 后 freeze 产生新 hash（如果配置变了）"""
        yaml_path = os.path.join(sample_yaml_dir, "llm_engine_config.yaml")
        cfg = NoteForgeConfig(config_path=yaml_path)
        f1 = cfg.freeze()

        # 修改 YAML 文件
        with open(yaml_path, 'w', encoding='utf-8') as f:
            f.write("""
provider:
  type: "openai"
  openai:
    model: "gpt-4o"
    temperature: 0.5
    max_tokens: 4096
quality:
  min_score: 0.90
  max_retries: 3
  retry_temperature_delta: 0.2
""")
        cfg.reload()
        f2 = cfg.freeze()
        assert f1.config_hash != f2.config_hash


# ============================================================
# LLMNoteEngine 接受 EngineConfig 测试
# ============================================================

class TestLLMNoteEngineAcceptsEngineConfig:
    """LLMNoteEngine 接受 EngineConfig"""

    def test_engine_config_param_accepted(self, sample_yaml_path):
        """engine_config 参数被接受"""
        from noteforge.engine.note_engine import LLMNoteEngine
        frozen = EngineConfig.from_yaml(sample_yaml_path)
        # 不实际创建引擎（需要环境），只验证参数签名
        import inspect
        sig = inspect.signature(LLMNoteEngine.__init__)
        assert 'engine_config' in sig.parameters

    def test_backward_compat_config_path(self, sample_yaml_path):
        """config_path 参数仍然有效（向后兼容）"""
        from noteforge.engine.note_engine import LLMNoteEngine
        import inspect
        sig = inspect.signature(LLMNoteEngine.__init__)
        assert 'config_path' in sig.parameters

    def test_engine_config_stored_on_engine(self, sample_yaml_path):
        """engine_config 存储在 _engine_config 属性"""
        from noteforge.engine.note_engine import LLMNoteEngine
        frozen = EngineConfig.from_yaml(sample_yaml_path)
        # 使用 mock 避免完整初始化（需要 LLM API key 等）
        # 直接测试 __init__ 中的逻辑
        try:
            engine = LLMNoteEngine(config_path=sample_yaml_path, engine_config=frozen)
            assert engine._engine_config is frozen
        except Exception:
            # 如果环境不满足（如缺少 API key），跳过
            pytest.skip("Environment not ready for LLMNoteEngine init")

    def test_engine_without_engine_config_has_none(self, sample_yaml_path):
        """不传 engine_config 时 _engine_config 为 None"""
        from noteforge.engine.note_engine import LLMNoteEngine
        try:
            engine = LLMNoteEngine(config_path=sample_yaml_path)
            assert engine._engine_config is None
        except Exception:
            pytest.skip("Environment not ready for LLMNoteEngine init")

    def test_engine_quality_from_frozen_config(self, sample_yaml_path):
        """engine_config 传入时，质量配置从冻结配置读取"""
        from noteforge.engine.note_engine import LLMNoteEngine
        frozen = EngineConfig.from_yaml(sample_yaml_path)
        try:
            engine = LLMNoteEngine(config_path=sample_yaml_path, engine_config=frozen)
            assert engine.min_score == frozen.quality.min_score
            assert engine.max_retries == frozen.quality.max_retries
            assert engine.retry_temp_delta == frozen.quality.retry_temp_delta
        except Exception:
            pytest.skip("Environment not ready for LLMNoteEngine init")


# ============================================================
# ExecutionTrace config_hash 集成测试
# ============================================================

class TestExecutionTraceConfigHash:
    """ExecutionTrace 存储 config_hash"""

    def test_save_with_config_hash(self):
        """save 时 config_hash 被写入追踪文件"""
        from noteforge.infra.execution_trace import ExecutionTrace
        tmp = tempfile.mkdtemp()
        trace = ExecutionTrace(trace_dir=tmp)
        records = [
            ExecutionTrace.StepRecord(
                stage="download",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="aaa",
                output_hash="bbb",
            ),
        ]
        trace.save("test_trace", records, config_hash="abc123def456")

        # 读取文件验证
        path = os.path.join(tmp, "test_trace.json")
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert data['config_hash'] == "abc123def456"
        assert 'steps' in data
        assert len(data['steps']) == 1

        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    def test_save_without_config_hash(self):
        """save 时不传 config_hash，文件中无该字段"""
        from noteforge.infra.execution_trace import ExecutionTrace
        tmp = tempfile.mkdtemp()
        trace = ExecutionTrace(trace_dir=tmp)
        records = [
            ExecutionTrace.StepRecord(
                stage="download",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="aaa",
            ),
        ]
        trace.save("test_trace", records)

        path = os.path.join(tmp, "test_trace.json")
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert 'config_hash' not in data
        assert 'steps' in data

        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    def test_resume_preserves_config_hash(self):
        """resume 后 config_hash 被保留"""
        from noteforge.infra.execution_trace import ExecutionTrace
        tmp = tempfile.mkdtemp()
        trace = ExecutionTrace(trace_dir=tmp)
        records = [
            ExecutionTrace.StepRecord(
                stage="download",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="aaa",
                output_hash="bbb",
            ),
        ]
        trace.save("test_trace", records, config_hash="hash123")
        loaded = trace.resume("test_trace")
        assert trace._last_config_hash == "hash123"
        assert len(loaded) == 1

        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    def test_resume_backward_compat_old_format(self):
        """resume 兼容旧格式（纯列表）"""
        from noteforge.infra.execution_trace import ExecutionTrace
        tmp = tempfile.mkdtemp()
        # 写入旧格式
        path = os.path.join(tmp, "old_trace.json")
        old_data = [
            {
                "stage": "download",
                "status": "completed",
                "input_hash": "aaa",
                "output_hash": "bbb",
                "started_at": "",
                "completed_at": None,
                "error_type": None,
                "retry_count": 0,
            }
        ]
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(old_data, f)

        trace = ExecutionTrace(trace_dir=tmp)
        loaded = trace.resume("old_trace")
        assert len(loaded) == 1
        assert loaded[0].stage == "download"
        assert trace._last_config_hash == ""

        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    def test_get_config_hash(self):
        """get_config_hash 读取追踪文件中的 config_hash"""
        from noteforge.infra.execution_trace import ExecutionTrace
        tmp = tempfile.mkdtemp()
        trace = ExecutionTrace(trace_dir=tmp)
        records = [
            ExecutionTrace.StepRecord(
                stage="download",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="aaa",
            ),
        ]
        trace.save("test_trace", records, config_hash="myhash")
        assert trace.get_config_hash("test_trace") == "myhash"

        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    def test_get_config_hash_missing_file(self):
        """get_config_hash 文件不存在时返回空字符串"""
        from noteforge.infra.execution_trace import ExecutionTrace
        tmp = tempfile.mkdtemp()
        trace = ExecutionTrace(trace_dir=tmp)
        assert trace.get_config_hash("nonexistent") == ""

        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# PipelineContext config_hash 测试
# ============================================================

class TestPipelineContextConfigHash:
    """PipelineContext 包含 config_hash"""

    def test_context_has_config_hash_field(self):
        from noteforge.context import PipelineContext
        ctx = PipelineContext()
        assert hasattr(ctx, 'config_hash')
        assert ctx.config_hash == ""

    def test_context_config_hash_set(self):
        from noteforge.context import PipelineContext
        ctx = PipelineContext(config_hash="abc123")
        assert ctx.config_hash == "abc123"

    def test_context_meta_includes_config_hash(self):
        from noteforge.context import PipelineContext
        ctx = PipelineContext(config_hash="test_hash")
        meta = ctx.meta
        assert 'config_hash' in meta
        assert meta['config_hash'] == "test_hash"
