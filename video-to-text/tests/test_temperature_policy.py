# -*- coding: utf-8 -*-
"""
GenerationConfig 温度策略测试

覆盖：
  - get_temperature_policy: 策略查询
  - should_freeze_temperature: 冻结判断
  - YAML 配置覆盖默认值
  - 向后兼容性
"""

import pytest
from noteforge.engine.stages.config import GenerationConfig, FACTUAL_CONTENT_TYPES


class TestTemperaturePolicy:
    """温度策略配置测试"""

    def test_default_factual_types_freeze(self):
        """默认配置：事实性内容类型应冻结温度"""
        cfg = GenerationConfig()
        for ct in FACTUAL_CONTENT_TYPES:
            assert cfg.get_temperature_policy(ct) == "freeze"
            assert cfg.should_freeze_temperature(ct) is True

    def test_default_creative_types_increment(self):
        """默认配置：创意性内容类型应递增温度"""
        cfg = GenerationConfig()
        assert cfg.get_temperature_policy("tutorial") == "increment"
        assert cfg.get_temperature_policy("meeting") == "increment"
        assert cfg.should_freeze_temperature("tutorial") is False
        assert cfg.should_freeze_temperature("meeting") is False

    def test_yaml_policy_overrides_default(self):
        """YAML 配置的策略应覆盖默认值"""
        cfg = GenerationConfig(
            temperature_policy={
                "lecture": "increment",  # 覆盖默认的 freeze
                "tutorial": "freeze",    # 覆盖默认的 increment
            }
        )
        assert cfg.get_temperature_policy("lecture") == "increment"
        assert cfg.should_freeze_temperature("lecture") is False
        assert cfg.get_temperature_policy("tutorial") == "freeze"
        assert cfg.should_freeze_temperature("tutorial") is True

    def test_unknown_type_defaults_to_increment(self):
        """未知内容类型应默认为 increment"""
        cfg = GenerationConfig()
        assert cfg.get_temperature_policy("unknown_type") == "increment"
        assert cfg.should_freeze_temperature("unknown_type") is False

    def test_freeze_disabled_all_increment(self):
        """freeze_temp_for_factual=False 时，所有类型都不冻结"""
        cfg = GenerationConfig(freeze_temp_for_factual=False)
        assert cfg.should_freeze_temperature("lecture") is False
        assert cfg.should_freeze_temperature("interview") is False

    def test_adaptive_policy_not_freeze(self):
        """adaptive 策略当前等同于不冻结（未来可扩展）"""
        cfg = GenerationConfig(
            temperature_policy={"lecture": "adaptive"}
        )
        assert cfg.get_temperature_policy("lecture") == "adaptive"
        assert cfg.should_freeze_temperature("lecture") is False

    def test_backward_compat_freeze_flag(self):
        """向后兼容：freeze_temp_for_factual 仍有效"""
        cfg = GenerationConfig(freeze_temp_for_factual=True)
        assert cfg.should_freeze_temperature("lecture") is True
        cfg2 = GenerationConfig(freeze_temp_for_factual=False)
        assert cfg2.should_freeze_temperature("lecture") is False
