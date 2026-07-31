# -*- coding: utf-8 -*-
"""
Integration test: Config runtime isolation

Tests the full chain:
  EngineConfig created from YAML → frozen (immutable)
  YAML file modified → EngineConfig still has original values
  config_hash detects the change

Uses real file I/O (tempdir) with no external services.
"""

import hashlib
import json
import os
import tempfile
from dataclasses import FrozenInstanceError

import pytest

from noteforge.config import EngineConfig, ProviderConfig, QualityConfig, load_yaml


pytestmark = pytest.mark.integration


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _write_yaml(path, content):
    """Write a YAML file."""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


MINIMAL_YAML = """\
provider:
  type: claude
  claude:
    model: claude-sonnet-4-20250514
    base_url: https://api.anthropic.com
    temperature: 0.3
    max_tokens: 8192
quality:
  min_score: 0.80
  max_retries: 2
  retry_temperature_delta: 0.1
content_type: lecture
"""

MODIFIED_YAML = """\
provider:
  type: openai
  openai:
    model: gpt-4o
    base_url: https://api.openai.com/v1
    temperature: 0.7
    max_tokens: 4096
quality:
  min_score: 0.90
  max_retries: 5
  retry_temperature_delta: 0.2
content_type: interview
"""


# ═══════════════════════════════════════════════════════════════
# EngineConfig is frozen
# ═══════════════════════════════════════════════════════════════


class TestEngineConfigFrozen:
    """EngineConfig is frozen (immutable) after creation."""

    def test_cannot_modify_content_type(self):
        """Cannot modify content_type on a frozen EngineConfig."""
        cfg = EngineConfig(
            provider=ProviderConfig(),
            quality=QualityConfig(),
            content_type="lecture",
        )
        with pytest.raises(FrozenInstanceError):
            cfg.content_type = "interview"

    def test_cannot_modify_config_hash(self):
        """Cannot modify config_hash on a frozen EngineConfig."""
        cfg = EngineConfig(
            provider=ProviderConfig(),
            quality=QualityConfig(),
            config_hash="abc123",
        )
        with pytest.raises(FrozenInstanceError):
            cfg.config_hash = "changed"

    def test_cannot_modify_provider(self):
        """Cannot replace provider on a frozen EngineConfig."""
        cfg = EngineConfig(
            provider=ProviderConfig(type="claude"),
            quality=QualityConfig(),
        )
        with pytest.raises(FrozenInstanceError):
            cfg.provider = ProviderConfig(type="openai")

    def test_cannot_modify_quality(self):
        """Cannot replace quality on a frozen EngineConfig."""
        cfg = EngineConfig(
            provider=ProviderConfig(),
            quality=QualityConfig(min_score=0.80),
        )
        with pytest.raises(FrozenInstanceError):
            cfg.quality = QualityConfig(min_score=0.90)

    def test_provider_config_is_frozen(self):
        """ProviderConfig is also frozen."""
        provider = ProviderConfig(type="claude", model="claude-sonnet-4-20250514")
        with pytest.raises(FrozenInstanceError):
            provider.model = "gpt-4o"

    def test_quality_config_is_frozen(self):
        """QualityConfig is also frozen."""
        quality = QualityConfig(min_score=0.80)
        with pytest.raises(FrozenInstanceError):
            quality.min_score = 0.90


# ═══════════════════════════════════════════════════════════════
# EngineConfig from YAML retains original values after YAML change
# ═══════════════════════════════════════════════════════════════


class TestConfigRuntimeIsolation:
    """EngineConfig retains original values even after YAML file is modified."""

    def test_config_keeps_original_values_after_yaml_change(self):
        """After modifying the YAML file, EngineConfig still has original values."""
        tmp = tempfile.mkdtemp()
        yaml_path = os.path.join(tmp, "config.yaml")

        # Write initial YAML
        _write_yaml(yaml_path, MINIMAL_YAML)

        # Create EngineConfig from YAML
        cfg = EngineConfig.from_yaml(yaml_path)

        # Record original values
        original_model = cfg.provider.model
        original_type = cfg.provider.type
        original_temp = cfg.provider.temperature
        original_max_tokens = cfg.provider.max_tokens
        original_min_score = cfg.quality.min_score
        original_content_type = cfg.content_type

        assert original_type == "claude"
        assert original_model == "claude-sonnet-4-20250514"
        assert original_temp == 0.3
        assert original_max_tokens == 8192
        assert original_min_score == 0.80
        assert original_content_type == "lecture"

        # Modify the YAML file on disk
        _write_yaml(yaml_path, MODIFIED_YAML)

        # EngineConfig still has original values (frozen snapshot)
        assert cfg.provider.model == original_model
        assert cfg.provider.type == original_type
        assert cfg.provider.temperature == original_temp
        assert cfg.provider.max_tokens == original_max_tokens
        assert cfg.quality.min_score == original_min_score
        assert cfg.content_type == original_content_type

    def test_new_config_from_modified_yaml_has_new_values(self):
        """A new EngineConfig created after YAML change has the new values."""
        tmp = tempfile.mkdtemp()
        yaml_path = os.path.join(tmp, "config.yaml")

        # Write initial YAML
        _write_yaml(yaml_path, MINIMAL_YAML)
        cfg_old = EngineConfig.from_yaml(yaml_path)

        # Modify the YAML file
        _write_yaml(yaml_path, MODIFIED_YAML)
        cfg_new = EngineConfig.from_yaml(yaml_path)

        # Old config retains original values
        assert cfg_old.provider.type == "claude"
        assert cfg_old.provider.model == "claude-sonnet-4-20250514"

        # New config has updated values
        assert cfg_new.provider.type == "openai"
        assert cfg_new.provider.model == "gpt-4o"
        assert cfg_new.provider.temperature == 0.7
        assert cfg_new.provider.max_tokens == 4096
        assert cfg_new.quality.min_score == 0.90
        assert cfg_new.quality.max_retries == 5
        assert cfg_new.content_type == "interview"

    def test_two_configs_from_same_yaml_are_equal(self):
        """Two EngineConfigs from the same YAML have identical values and hash."""
        tmp = tempfile.mkdtemp()
        yaml_path = os.path.join(tmp, "config.yaml")
        _write_yaml(yaml_path, MINIMAL_YAML)

        cfg1 = EngineConfig.from_yaml(yaml_path)
        cfg2 = EngineConfig.from_yaml(yaml_path)

        assert cfg1.provider.model == cfg2.provider.model
        assert cfg1.config_hash == cfg2.config_hash


# ═══════════════════════════════════════════════════════════════
# config_hash detects change
# ═══════════════════════════════════════════════════════════════


class TestConfigHashDetectsChange:
    """config_hash detects when the YAML configuration has changed."""

    def test_different_configs_have_different_hashes(self):
        """EngineConfigs from different YAMLs have different config_hash values."""
        tmp = tempfile.mkdtemp()
        yaml_path = os.path.join(tmp, "config.yaml")

        _write_yaml(yaml_path, MINIMAL_YAML)
        cfg_old = EngineConfig.from_yaml(yaml_path)

        _write_yaml(yaml_path, MODIFIED_YAML)
        cfg_new = EngineConfig.from_yaml(yaml_path)

        assert cfg_old.config_hash != cfg_new.config_hash

    def test_hash_changes_on_model_change(self):
        """config_hash changes when only the model is changed."""
        tmp = tempfile.mkdtemp()
        yaml_path = os.path.join(tmp, "config.yaml")

        _write_yaml(yaml_path, MINIMAL_YAML)
        cfg1 = EngineConfig.from_yaml(yaml_path)

        # Change only the model
        modified = MINIMAL_YAML.replace("claude-sonnet-4-20250514", "claude-haiku-4-20250414")
        _write_yaml(yaml_path, modified)
        cfg2 = EngineConfig.from_yaml(yaml_path)

        assert cfg1.config_hash != cfg2.config_hash

    def test_hash_changes_on_temperature_change(self):
        """config_hash changes when only temperature is changed."""
        tmp = tempfile.mkdtemp()
        yaml_path = os.path.join(tmp, "config.yaml")

        _write_yaml(yaml_path, MINIMAL_YAML)
        cfg1 = EngineConfig.from_yaml(yaml_path)

        modified = MINIMAL_YAML.replace("temperature: 0.3", "temperature: 0.5")
        _write_yaml(yaml_path, modified)
        cfg2 = EngineConfig.from_yaml(yaml_path)

        assert cfg1.config_hash != cfg2.config_hash

    def test_hash_changes_on_quality_change(self):
        """config_hash changes when quality settings are changed."""
        tmp = tempfile.mkdtemp()
        yaml_path = os.path.join(tmp, "config.yaml")

        _write_yaml(yaml_path, MINIMAL_YAML)
        cfg1 = EngineConfig.from_yaml(yaml_path)

        modified = MINIMAL_YAML.replace("min_score: 0.80", "min_score: 0.95")
        _write_yaml(yaml_path, modified)
        cfg2 = EngineConfig.from_yaml(yaml_path)

        assert cfg1.config_hash != cfg2.config_hash

    def test_hash_stable_for_same_config(self):
        """config_hash is stable (deterministic) for the same configuration."""
        cfg1 = EngineConfig._from_raw({
            "provider": {
                "type": "claude",
                "claude": {
                    "model": "claude-sonnet-4-20250514",
                    "base_url": "https://api.anthropic.com",
                    "temperature": 0.3,
                    "max_tokens": 8192,
                },
            },
            "quality": {
                "min_score": 0.80,
                "max_retries": 2,
                "retry_temperature_delta": 0.1,
            },
        })
        cfg2 = EngineConfig._from_raw({
            "provider": {
                "type": "claude",
                "claude": {
                    "model": "claude-sonnet-4-20250514",
                    "base_url": "https://api.anthropic.com",
                    "temperature": 0.3,
                    "max_tokens": 8192,
                },
            },
            "quality": {
                "min_score": 0.80,
                "max_retries": 2,
                "retry_temperature_delta": 0.1,
            },
        })
        assert cfg1.config_hash == cfg2.config_hash

    def test_hash_is_16_chars(self):
        """config_hash is a 16-character hex string (truncated SHA-256)."""
        cfg = EngineConfig._from_raw({
            "provider": {"type": "claude", "claude": {"model": "test"}},
            "quality": {},
        })
        assert len(cfg.config_hash) == 16
        # All hex characters
        assert all(c in "0123456789abcdef" for c in cfg.config_hash)

    def test_to_dict_roundtrip(self):
        """EngineConfig.to_dict() produces a serializable dict."""
        cfg = EngineConfig._from_raw({
            "provider": {
                "type": "claude",
                "claude": {
                    "model": "claude-sonnet-4-20250514",
                    "base_url": "https://api.anthropic.com",
                    "temperature": 0.3,
                    "max_tokens": 8192,
                },
            },
            "quality": {
                "min_score": 0.80,
                "max_retries": 2,
                "retry_temperature_delta": 0.1,
            },
        })
        d = cfg.to_dict()
        assert isinstance(d, dict)
        assert d["provider"]["type"] == "claude"
        assert d["quality"]["min_score"] == 0.80
        assert d["config_hash"] == cfg.config_hash

        # Can be serialized to JSON
        json_str = json.dumps(d)
        assert isinstance(json_str, str)
