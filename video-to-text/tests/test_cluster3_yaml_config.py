"""
Cluster 3: YAML 配置化测试

覆盖:
  - 清洗规则从 YAML 加载
  - 回退到默认规则
  - 格式模板从 YAML 加载
  - PromptBuilder 使用 YAML 格式模板

运行:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_cluster3_yaml_config.py -v
"""
import os
import tempfile
import yaml
import pytest
from pathlib import Path

from noteforge.core.transcript_preprocessor import TranscriptPreprocessor
from noteforge.core.prompt_builder import PromptBuilder


# ================================================================
# 清洗规则 (TranscriptPreprocessor) 测试
# ================================================================

class TestCleaningRulesYAML:
    """清洗规则从 YAML 加载 + 回退默认"""

    def _write_cleaning_rules_yaml(self, noise_patterns, filler_patterns) -> str:
        """写入临时 YAML 文件，返回路径"""
        data = {
            'noise_patterns': noise_patterns,
            'filler_patterns': filler_patterns,
        }
        fd, path = tempfile.mkstemp(suffix='.yaml', prefix='cleaning_rules_')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True)
        return path

    def test_default_no_path(self):
        """不传路径时应使用默认规则"""
        p = TranscriptPreprocessor()
        text = "大家好[无法识别片段]嗯[00:01]今天"
        cleaned = p.clean(text, clean_fillers=True, clean_timestamps=True)
        assert "[无法识别片段]" not in cleaned
        assert "[00:01]" not in cleaned
        assert "嗯" not in cleaned

    def test_nonexistent_path_falls_back(self):
        """不存在的 YAML 路径应回退到默认规则"""
        p = TranscriptPreprocessor(cleaning_rules_path="/nonexistent/path.yaml")
        text = "大家好[无法识别片段]嗯"
        cleaned = p.clean(text, clean_fillers=True, clean_unrecognized=True)
        assert "[无法识别片段]" not in cleaned
        assert "嗯" not in cleaned

    def test_load_from_valid_yaml(self):
        """有效 YAML 应正确加载自定义规则"""
        # 自定义规则：移除 "ABC"，保留默认噪声
        yaml_path = self._write_cleaning_rules_yaml(
            noise_patterns=[
                {'pattern': r'\[无法识别片段\]', 'replace': ''},
                {'pattern': r'ABC', 'replace': '[已替换]'},
            ],
            filler_patterns=[
                {'pattern': r'嗯', 'replace': ''},
            ],
        )
        try:
            p = TranscriptPreprocessor(cleaning_rules_path=yaml_path)
            text = "大家好[无法识别片段]ABC嗯"
            cleaned = p.clean(text, clean_fillers=True, clean_unrecognized=True)
            assert "[已替换]" in cleaned
            assert "[无法识别片段]" not in cleaned
            assert "嗯" not in cleaned
        finally:
            os.unlink(yaml_path)

    def test_custom_rule_overrides_default(self):
        """YAML 规则应完全替换默认（不追加），如果 YAML 提供部分规则"""
        yaml_path = self._write_cleaning_rules_yaml(
            # 只提供一条噪声规则，不提供时间戳规则
            noise_patterns=[
                {'pattern': r'\[测试标记\]', 'replace': ''},
            ],
            filler_patterns=[
                {'pattern': r'嗯', 'replace': ''},
            ],
        )
        try:
            p = TranscriptPreprocessor(cleaning_rules_path=yaml_path)
            # 默认的时间戳规则不应生效（YAML 规则替换了默认）
            text = "[00:01][测试标记]嗯"
            cleaned = p.clean(text, clean_fillers=True, clean_timestamps=True)
            assert "[测试标记]" not in cleaned
            # 时间戳保留（因为 YAML 中没有这条规则）
            assert "[00:01]" in cleaned
            assert "嗯" not in cleaned
        finally:
            os.unlink(yaml_path)

    def test_empty_yaml_falls_back_to_defaults(self):
        """空 YAML 文件应回退到默认规则"""
        data = {}
        fd, path = tempfile.mkstemp(suffix='.yaml', prefix='empty_')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            yaml.dump(data, f)
        try:
            p = TranscriptPreprocessor(cleaning_rules_path=path)
            text = "大家好[无法识别片段]嗯"
            cleaned = p.clean(text, clean_fillers=True, clean_unrecognized=True)
            assert "[无法识别片段]" not in cleaned
            assert "嗯" not in cleaned
        finally:
            os.unlink(path)

    def test_malformed_yaml_falls_back(self):
        """损坏的 YAML 应回退到默认规则"""
        fd, path = tempfile.mkstemp(suffix='.yaml', prefix='bad_')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write("{{{{invalid yaml")
        try:
            p = TranscriptPreprocessor(cleaning_rules_path=path)
            text = "大家好[无法识别片段]嗯"
            cleaned = p.clean(text, clean_fillers=True, clean_unrecognized=True)
            assert "[无法识别片段]" not in cleaned
            assert "嗯" not in cleaned
        finally:
            os.unlink(path)

    def test_cleaning_behavior_identical_to_default(self):
        """使用默认规则的 TranscriptPreprocessor 与旧行为一致"""
        p = TranscriptPreprocessor()
        text = "大家好[无法识别片段][00:01:23]<0.5>嗯那个就是说"
        cleaned = p.clean(text, clean_fillers=True, clean_unrecognized=True, clean_timestamps=True)
        assert "[无法识别片段]" not in cleaned
        assert "[00:01:23]" not in cleaned
        assert "<0.5>" not in cleaned
        assert "嗯" not in cleaned
        assert "就是说" not in cleaned


# ================================================================
# 格式模板 (PromptBuilder) 测试
# ================================================================

class TestFormatTemplatesYAML:
    """格式模板从 YAML 加载 + 回退默认"""

    def _write_format_templates_yaml(self, templates: dict) -> str:
        """写入临时 YAML 文件，返回路径"""
        data = {'format_templates': templates}
        fd, path = tempfile.mkstemp(suffix='.yaml', prefix='format_templates_')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True)
        return path

    def test_default_format_section_contains_output_format(self):
        """默认格式 section 应包含输出格式要求"""
        config_dir = Path(__file__).parent.parent / "config"
        rules = str(config_dir / "note_generation_rules.yaml")
        exp = str(config_dir / "experience_log.yaml")
        if not Path(rules).exists():
            pytest.skip("Config files not found")
        builder = PromptBuilder(rules, exp, content_type='lecture')
        fmt = builder._build_format_section()
        assert "输出格式要求" in fmt
        assert "行动清单" in fmt
        assert "金句摘录" in fmt

    def test_default_format_section_content_type(self):
        """默认格式 section 应包含内容类型"""
        config_dir = Path(__file__).parent.parent / "config"
        rules = str(config_dir / "note_generation_rules.yaml")
        exp = str(config_dir / "experience_log.yaml")
        if not Path(rules).exists():
            pytest.skip("Config files not found")
        builder = PromptBuilder(rules, exp, content_type='lecture')
        fmt = builder._build_format_section()
        assert "lecture" in fmt

    def test_custom_template_from_yaml(self):
        """自定义 YAML 模板应覆盖默认模板"""
        config_dir = Path(__file__).parent.parent / "config"
        rules = str(config_dir / "note_generation_rules.yaml")
        exp = str(config_dir / "experience_log.yaml")
        if not Path(rules).exists():
            pytest.skip("Config files not found")

        yaml_path = self._write_format_templates_yaml({
            'output_format_header': (
                "## 自定义输出格式\n\n"
                "内容类型: __CONTENT_TYPE__\n\n"
                "__CONTENT_SECTIONS__"
            ),
        })
        try:
            builder = PromptBuilder(
                rules, exp, content_type='lecture',
                format_templates_path=yaml_path,
            )
            fmt = builder._build_format_section()
            assert "自定义输出格式" in fmt
            assert "lecture" in fmt
        finally:
            os.unlink(yaml_path)

    def test_partial_yaml_template_merges_with_defaults(self):
        """部分 YAML 模板应与默认模板合并"""
        config_dir = Path(__file__).parent.parent / "config"
        rules = str(config_dir / "note_generation_rules.yaml")
        exp = str(config_dir / "experience_log.yaml")
        if not Path(rules).exists():
            pytest.skip("Config files not found")

        # 只提供 action_list_format，其他应使用默认
        yaml_path = self._write_format_templates_yaml({
            'action_list_format': "### 自定义行动清单格式\n",
        })
        try:
            builder = PromptBuilder(
                rules, exp, content_type='lecture',
                format_templates_path=yaml_path,
            )
            fmt = builder._build_format_section()
            # 自定义部分
            assert "自定义行动清单格式" in fmt
            # 默认部分应保留
            assert "金句摘录" in fmt
            assert "转写质量声明" in fmt
        finally:
            os.unlink(yaml_path)

    def test_nonexistent_path_uses_defaults(self):
        """不存在的 YAML 路径应使用默认模板"""
        config_dir = Path(__file__).parent.parent / "config"
        rules = str(config_dir / "note_generation_rules.yaml")
        exp = str(config_dir / "experience_log.yaml")
        if not Path(rules).exists():
            pytest.skip("Config files not found")
        builder = PromptBuilder(
            rules, exp, content_type='lecture',
            format_templates_path="/nonexistent/templates.yaml",
        )
        fmt = builder._build_format_section()
        assert "输出格式要求" in fmt
        assert "行动清单" in fmt

    def test_format_section_includes_content_sections(self):
        """格式 section 应包含动态内容节列表"""
        config_dir = Path(__file__).parent.parent / "config"
        rules = str(config_dir / "note_generation_rules.yaml")
        exp = str(config_dir / "experience_log.yaml")
        if not Path(rules).exists():
            pytest.skip("Config files not found")
        builder = PromptBuilder(rules, exp, content_type='lecture')
        fmt = builder._build_format_section()
        # lecture 类型的必需节应出现
        assert "核心观点" in fmt
        assert "学习总结" in fmt
