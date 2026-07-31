# -*- coding: utf-8 -*-
"""
NoteForge 集中配置管理

提供统一的 YAML/JSON 配置加载、PathConfig 构建和配置访问接口。
替代之前散落在 engine.__init__、CLI 和各模块中的独立加载逻辑。

冻结配置（EngineConfig）：
  ProviderConfig / QualityConfig / EngineConfig 是 frozen dataclass，
  一旦创建不可修改，确保运行中的流水线不受配置热更新的影响。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, asdict
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


# ============================================================
# 冻结配置（frozen dataclass）— 运行时不可变快照
# ============================================================

@dataclass(frozen=True)
class ProviderConfig:
    """LLM 提供商冻结配置"""
    type: str = "claude"
    model: str = ""
    base_url: str = ""
    temperature: float = 0.3
    max_tokens: int = 8192


@dataclass(frozen=True)
class QualityConfig:
    """质量门禁冻结配置"""
    min_score: float = 0.80
    max_retries: int = 2
    retry_temp_delta: float = 0.1


@dataclass(frozen=True)
class EngineConfig:
    """引擎冻结配置 — 不可变快照，保证运行中的流水线不受配置热更新影响

    用法:
        cfg = NoteForgeConfig()
        frozen = cfg.freeze()           # 从当前配置创建快照
        engine = LLMNoteEngine(engine_config=frozen)  # 传入冻结配置

        # 或从 YAML 直接创建
        frozen = EngineConfig.from_yaml("config/llm_engine_config.yaml")
    """
    provider: ProviderConfig
    quality: QualityConfig
    content_type: str = "lecture"
    config_hash: str = ""

    @classmethod
    def from_yaml(cls, config_path: str) -> 'EngineConfig':
        """从 YAML 文件加载并冻结配置

        Args:
            config_path: YAML 配置文件路径

        Returns:
            冻结的 EngineConfig 实例
        """
        raw = load_yaml(config_path)
        return cls._from_raw(raw, config_path=config_path)

    @classmethod
    def _from_raw(cls, raw: Dict[str, Any], config_path: str = "") -> 'EngineConfig':
        """从原始配置字典构建冻结配置（内部方法）"""
        # Provider
        provider_raw = raw.get('provider', {})
        provider_type = provider_raw.get('type', 'claude')
        # 嵌套的提供商特定配置
        provider_specific = provider_raw.get(provider_type, {})
        provider_cfg = ProviderConfig(
            type=provider_type,
            model=provider_specific.get('model', ''),
            base_url=provider_specific.get('base_url', ''),
            temperature=provider_specific.get('temperature', 0.3),
            max_tokens=provider_specific.get('max_tokens', 8192),
        )

        # Quality
        quality_raw = raw.get('quality', {})
        quality_cfg = QualityConfig(
            min_score=quality_raw.get('min_score', 0.80),
            max_retries=quality_raw.get('max_retries', 2),
            retry_temp_delta=quality_raw.get('retry_temperature_delta', 0.1),
        )

        # Config hash — 基于影响引擎行为的字段计算
        hash_payload = json.dumps({
            'provider_type': provider_cfg.type,
            'provider_model': provider_cfg.model,
            'provider_base_url': provider_cfg.base_url,
            'provider_temperature': provider_cfg.temperature,
            'provider_max_tokens': provider_cfg.max_tokens,
            'quality_min_score': quality_cfg.min_score,
            'quality_max_retries': quality_cfg.max_retries,
            'quality_retry_temp_delta': quality_cfg.retry_temp_delta,
        }, sort_keys=True)
        config_hash = hashlib.sha256(hash_payload.encode('utf-8')).hexdigest()[:16]

        return cls(
            provider=provider_cfg,
            quality=quality_cfg,
            content_type=raw.get('content_type', 'lecture'),
            config_hash=config_hash,
        )

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于 ExecutionTrace 存储）"""
        return asdict(self)


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
        """质量门禁配置"""
        return self._config.get('quality', {})

    @property
    def domains(self) -> list:
        """知识域列表"""
        return self._config.get('knowledge_domains', [])

    @property
    def feishu(self) -> Dict[str, Any]:
        """飞书同步配置"""
        return self._config.get('feishu', {})

    @property
    def provider(self) -> Dict[str, Any]:
        """LLM 提供商配置"""
        return self._config.get('provider', {})

    @property
    def prompts(self) -> Dict[str, Any]:
        """Prompt 配置"""
        return self._config.get('prompts', {})

    def __repr__(self) -> str:
        return f"NoteForgeConfig({self._config_path})"

    def freeze(self, content_type: str = "lecture") -> EngineConfig:
        """创建当前配置的不可变快照

        Args:
            content_type: 内容类型（lecture/tutorial/interview/podcast/meeting）

        Returns:
            冻结的 EngineConfig 实例，后续对 NoteForgeConfig 的修改不影响已冻结的快照
        """
        engine_cfg = EngineConfig._from_raw(self._config, config_path=self._config_path)
        # 覆盖 content_type（冻结时刻的值，而非 YAML 中的默认值）
        return EngineConfig(
            provider=engine_cfg.provider,
            quality=engine_cfg.quality,
            content_type=content_type,
            config_hash=engine_cfg.config_hash,
        )
