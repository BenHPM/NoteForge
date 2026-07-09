# -*- coding: utf-8 -*-
"""
NoteForge 集中配置管理

提供统一的 YAML/JSON 配置加载、PathConfig 构建和配置访问接口。
替代之前散落在 engine.__init__、CLI 和各模块中的独立加载逻辑。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from noteforge.context import PathConfig


def load_yaml(path: str) -> Dict[str, Any]:
    """加载 YAML 配置文件，缺失时返回空字典"""
    if not os.path.exists(path):
        return {}
    try:
        import yaml
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def load_json(path: str) -> Dict[str, Any]:
    """加载 JSON 配置文件，缺失时返回空字典"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def build_path_config(config: Dict[str, Any], base_dir: Path) -> PathConfig:
    """从配置字典构建 PathConfig"""
    paths = config.get('paths', {})
    log_dir = config.get('logging', {}).get('log_dir', 'output/logs')
    return PathConfig(
        base_dir=base_dir,
        transcripts_dir=base_dir / paths.get('transcripts_dir', 'output/transcripts'),
        notes_dir=base_dir / paths.get('notes_dir', 'output/notes'),
        reports_dir=base_dir / paths.get('reports_dir', 'output/quality_reports'),
        logs_dir=base_dir / log_dir,
    )


class NoteForgeConfig:
    """集中配置管理器

    加载 llm_engine_config.yaml 并提供便捷的属性访问。

    Usage:
        cfg = NoteForgeConfig()  # 默认 config/llm_engine_config.yaml
        cfg.get('quality.min_score', 0.8)  # 安全取值
        cfg.path_config  # PathConfig 实例
    """

    def __init__(self, config_path: Optional[str] = None, base_dir: Optional[Path] = None):
        if config_path is None:
            config_path = str((base_dir or Path(__file__).resolve().parents[1]) / "config" / "llm_engine_config.yaml")
        self._config_path = config_path
        self._base_dir = base_dir or Path(__file__).resolve().parents[1]
        self._config: Dict[str, Any] = {}
        self._path_config: Optional[PathConfig] = None
        self.reload()

    def reload(self) -> None:
        """重新从磁盘加载配置"""
        self._config = load_yaml(self._config_path)
        self._path_config = build_path_config(self._config, self._base_dir)

    # -- dict-like access --
    def get(self, path: str, default: Any = None) -> Any:
        """按点分路径取值，如 cfg.get('quality.min_score', 0.8)"""
        parts = path.split('.')
        val = self._config
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                return default
            if val is None:
                return default
        return val if val is not None else default

    @property
    def raw(self) -> Dict[str, Any]:
        """原始配置字典"""
        return self._config

    @property
    def path_config(self) -> PathConfig:
        """共享路径配置"""
        return self._path_config

    # -- 便捷属性 --
    @property
    def quality(self) -> Dict[str, Any]:
        return self._config.get('quality', {})

    @property
    def domains(self) -> list:
        return self._config.get('knowledge_domains', [])

    @property
    def feishu(self) -> Dict[str, Any]:
        return self._config.get('feishu', {})

    @property
    def provider(self) -> Dict[str, Any]:
        return self._config.get('provider', {})

    @property
    def prompts(self) -> Dict[str, Any]:
        return self._config.get('prompts', {})

    def __repr__(self) -> str:
        return f"NoteForgeConfig({self._config_path})"
