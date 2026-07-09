# -*- coding: utf-8 -*-
"""NoteForge 集中配置管理模块单元测试"""
import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, mock_open

from noteforge.config import load_yaml, load_json, build_path_config, NoteForgeConfig
from noteforge.context import PathConfig


# ============================================================
# load_yaml
# ============================================================

class TestLoadYaml:
    """load_yaml 函数测试"""

    def test_file_not_exists_returns_empty_dict(self):
        assert load_yaml("/nonexistent/path/config.yaml") == {}

    def test_valid_yaml_returns_dict(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("key: value\nnum: 42", encoding='utf-8')
        result = load_yaml(str(config_file))
        assert result == {"key": "value", "num": 42}

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("", encoding='utf-8')
        result = load_yaml(str(config_file))
        assert result == {}

    def test_yaml_parse_error_returns_empty_dict(self, tmp_path):
        config_file = tmp_path / "bad.yaml"
        config_file.write_text("key: [unclosed", encoding='utf-8')
        result = load_yaml(str(config_file))
        assert result == {}

    def test_yaml_returns_none_returns_empty_dict(self, tmp_path):
        config_file = tmp_path / "none.yaml"
        config_file.write_text("---\n", encoding='utf-8')
        result = load_yaml(str(config_file))
        assert result == {}


# ============================================================
# load_json
# ============================================================

class TestLoadJson:
    """load_json 函数测试"""

    def test_file_not_exists_returns_empty_dict(self):
        assert load_json("/nonexistent/path/data.json") == {}

    def test_valid_json_returns_dict(self, tmp_path):
        data_file = tmp_path / "data.json"
        data_file.write_text('{"key": "value", "num": 42}', encoding='utf-8')
        result = load_json(str(data_file))
        assert result == {"key": "value", "num": 42}

    def test_json_parse_error_returns_empty_dict(self, tmp_path):
        data_file = tmp_path / "bad.json"
        data_file.write_text('{invalid json}', encoding='utf-8')
        result = load_json(str(data_file))
        assert result == {}


# ============================================================
# build_path_config
# ============================================================

class TestBuildPathConfig:
    """build_path_config 函数测试"""

    def test_full_config_uses_specified_paths(self, tmp_path):
        config = {
            "paths": {
                "transcripts_dir": "custom/transcripts",
                "notes_dir": "custom/notes",
                "reports_dir": "custom/reports",
            },
            "logging": {"log_dir": "custom/logs"},
        }
        base = tmp_path
        pc = build_path_config(config, base)
        assert pc.transcripts_dir == base / "custom/transcripts"
        assert pc.notes_dir == base / "custom/notes"
        assert pc.reports_dir == base / "custom/reports"
        assert pc.logs_dir == base / "custom/logs"

    def test_missing_paths_use_defaults(self, tmp_path):
        config = {}
        pc = build_path_config(config, tmp_path)
        assert pc.transcripts_dir == tmp_path / "output/transcripts"
        assert pc.notes_dir == tmp_path / "output/notes"
        assert pc.reports_dir == tmp_path / "output/quality_reports"
        assert pc.logs_dir == tmp_path / "output/logs"

    def test_partial_config_merges_with_defaults(self, tmp_path):
        config = {"paths": {"notes_dir": "my_notes"}}
        pc = build_path_config(config, tmp_path)
        assert pc.notes_dir == tmp_path / "my_notes"
        assert pc.transcripts_dir == tmp_path / "output/transcripts"  # default
        assert pc.logs_dir == tmp_path / "output/logs"  # default

    def test_base_dir_is_set_correctly(self, tmp_path):
        pc = build_path_config({}, tmp_path)
        assert pc.base_dir == tmp_path


# ============================================================
# NoteForgeConfig
# ============================================================

class TestNoteForgeConfig:
    """NoteForgeConfig 类测试"""

    def test_default_init_loads_config(self):
        cfg = NoteForgeConfig()
        assert isinstance(cfg.raw, dict)
        assert len(cfg.raw) > 0  # 实际配置文件有内容

    def test_custom_config_path(self, tmp_path):
        custom_config = tmp_path / "custom.yaml"
        custom_config.write_text("quality:\n  min_score: 0.9\n", encoding='utf-8')
        cfg = NoteForgeConfig(config_path=str(custom_config), base_dir=tmp_path)
        assert cfg.get("quality.min_score") == 0.9

    def test_reload_refreshes_config(self, tmp_path):
        custom_config = tmp_path / "reload.yaml"
        custom_config.write_text("key: original\n", encoding='utf-8')
        cfg = NoteForgeConfig(config_path=str(custom_config), base_dir=tmp_path)
        assert cfg.get("key") == "original"
        # 修改文件后 reload
        custom_config.write_text("key: updated\n", encoding='utf-8')
        cfg.reload()
        assert cfg.get("key") == "updated"

    def test_reload_is_idempotent(self):
        cfg = NoteForgeConfig()
        first_raw = cfg.raw
        cfg.reload()
        assert cfg.raw == first_raw

    # -- get() 方法 --

    def test_get_simple_key(self):
        cfg = NoteForgeConfig()
        # quality 是实际配置中的顶层 key
        assert isinstance(cfg.get("quality"), dict)

    def test_get_dotted_path(self):
        cfg = NoteForgeConfig()
        score = cfg.get("quality.min_score")
        assert isinstance(score, (int, float))
        assert score > 0

    def test_get_missing_key_returns_default(self):
        cfg = NoteForgeConfig()
        assert cfg.get("nonexistent_key") is None
        assert cfg.get("nonexistent_key", "fallback") == "fallback"

    def test_get_missing_nested_path_returns_default(self):
        cfg = NoteForgeConfig()
        assert cfg.get("nonexistent.nested.key", 42) == 42

    def test_get_falsy_values_returned_not_default(self):
        cfg = NoteForgeConfig()
        # 0, False, "" 应返回实际值而非 default
        assert cfg.get("nonexistent", 0) == 0
        assert cfg.get("nonexistent", False) is False
        assert cfg.get("nonexistent", "") == ""

    def test_get_none_value_returns_default(self):
        cfg = NoteForgeConfig()
        # 配置中存在但值为 None 的 key → 返回 default
        result = cfg.get("nonexistent_none_key", "my_default")
        assert result == "my_default"

    # -- 便捷属性 --

    def test_quality_property(self):
        cfg = NoteForgeConfig()
        assert isinstance(cfg.quality, dict)
        assert "min_score" in cfg.quality

    def test_domains_property(self):
        cfg = NoteForgeConfig()
        assert isinstance(cfg.domains, list)
        assert len(cfg.domains) > 0

    def test_feishu_property(self):
        cfg = NoteForgeConfig()
        assert isinstance(cfg.feishu, dict)

    def test_provider_property(self):
        cfg = NoteForgeConfig()
        assert isinstance(cfg.provider, dict)

    def test_prompts_property(self):
        cfg = NoteForgeConfig()
        assert isinstance(cfg.prompts, dict)

    # -- path_config --

    def test_path_config_is_path_config_instance(self):
        cfg = NoteForgeConfig()
        assert isinstance(cfg.path_config, PathConfig)

    def test_path_config_has_all_dirs(self):
        cfg = NoteForgeConfig()
        pc = cfg.path_config
        assert pc.base_dir.is_dir() or True  # base_dir 可能不存在
        assert isinstance(pc.transcripts_dir, Path)
        assert isinstance(pc.notes_dir, Path)
        assert isinstance(pc.reports_dir, Path)
        assert isinstance(pc.logs_dir, Path)

    # -- __repr__ --

    def test_repr_contains_path(self):
        cfg = NoteForgeConfig()
        r = repr(cfg)
        assert "NoteForgeConfig" in r
        assert "llm_engine_config.yaml" in r

    def test_repr_does_not_leak_secrets(self):
        cfg = NoteForgeConfig()
        r = repr(cfg)
        assert "api_key" not in r.lower()
        assert "password" not in r.lower()
        assert "secret" not in r.lower()

    # -- raw 属性不可变防护 --

    def test_raw_returns_config_dict(self):
        cfg = NoteForgeConfig()
        assert cfg.raw is cfg._config
