"""
QualityGate 配置行为单元测试

覆盖：
  - 默认启用致命规则检查
  - 可关闭致命规则检查

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_quality_gate_config.py -v
"""
import os
import pytest

os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


class TestQualityGateConfig:
    """测试 QualityGate 可配置行为"""

    def test_fatal_rules_must_pass_default(self):
        """默认应启用致命规则检查"""
        from noteforge.quality.gate import QualityGate
        gate = QualityGate()
        assert gate._fatal_rules_must_pass is True

    def test_fatal_rules_must_pass_disabled(self):
        """应可关闭致命规则检查"""
        from noteforge.quality.gate import QualityGate
        gate = QualityGate(fatal_rules_must_pass=False)
        assert gate._fatal_rules_must_pass is False
