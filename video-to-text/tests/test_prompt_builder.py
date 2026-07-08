"""
NoteForge Prompt 组装模块单元测试

覆盖：
  - PromptBuilder: 各内容类型的 system/user/feedback prompt 组装
  - 内容类型配置验证
  - YAML 加载
  - 会议纪要专用 prompt

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_prompt_builder.py -v
"""
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# 跳过环境检查
os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


class TestPromptBuilder:
    """PromptBuilder 核心功能测试"""

    def setup_method(self):
        self.config_dir = Path(__file__).parent.parent / "config"
        self.rules_path = str(self.config_dir / "note_generation_rules.yaml")
        self.experience_path = str(self.config_dir / "experience_log.yaml")
        if not Path(self.rules_path).exists():
            pytest.skip("Config files not found")

    def test_lecture_content_type(self):
        """lecture 类型的 system prompt 应包含知识提炼相关内容"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        system = builder.build_system_prompt()
        assert '提炼' in system or '知识' in system

    def test_interview_content_type(self):
        """interview 类型的 system prompt 应包含访谈/嘉宾相关内容"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='interview')
        system = builder.build_system_prompt()
        assert '访谈' in system or '嘉宾' in system

    def test_user_prompt_with_content(self):
        """user prompt 应包含标题和转写内容"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        user = builder.build_user_prompt("Test content", title="测试标题")
        assert '测试标题' in user or 'Test content' in user

    def test_feedback_prompt(self):
        """feedback prompt 应包含问题描述"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        quality_report = {
            'total_score': 0.65,
            'overall_passed': False,
            'rule_results': {
                'R1': {
                    'issues': [{
                        'severity': 'fatal',
                        'description': '虚构了不存在的数据',
                        'suggestion': '删除虚构数据或标注来源',
                    }]
                }
            }
        }
        feedback = builder.build_feedback_prompt("转写原文内容", "之前的笔记内容", quality_report)
        assert '问题' in feedback or '反馈' in feedback

    def test_tutorial_content_type(self):
        """tutorial 类型的 system prompt 应包含实操相关内容"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='tutorial')
        system = builder.build_system_prompt()
        assert '实操' in system or '步骤' in system

    def test_podcast_content_type(self):
        """podcast 类型的 system prompt 应包含播客/发言者相关内容"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='podcast')
        system = builder.build_system_prompt()
        assert '播客' in system or '发言者' in system

    def test_meeting_content_type(self):
        """meeting 类型的 system prompt 应包含会议相关内容"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='meeting')
        system = builder.build_system_prompt()
        assert '会议' in system or '纪要' in system

    def test_invalid_content_type_defaults_to_lecture(self):
        """无效内容类型应默认回退为 lecture"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='nonexistent')
        assert builder.content_type == 'lecture'

    def test_system_prompt_contains_rules(self):
        """system prompt 应包含硬约束规则"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        system = builder.build_system_prompt()
        assert '硬约束' in system or 'R1' in system

    def test_system_prompt_contains_selfcheck(self):
        """system prompt 应包含自检清单"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        system = builder.build_system_prompt()
        assert '自检' in system

    def test_system_prompt_contains_content_source(self):
        """system prompt 应包含内容来源说明"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        system = builder.build_system_prompt()
        assert '内容来源' in system or '公开' in system

    def test_user_prompt_without_title(self):
        """无标题时 user prompt 不应包含标题行"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        user = builder.build_user_prompt("Test content")
        assert '来源标题' not in user

    def test_user_prompt_with_title(self):
        """有标题时 user prompt 应包含标题"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        user = builder.build_user_prompt("Test content", title="EP01测试")
        assert 'EP01测试' in user

    def test_feedback_prompt_includes_score(self):
        """feedback prompt 应包含质量得分"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        quality_report = {
            'total_score': 0.65,
            'overall_passed': False,
            'rule_results': {}
        }
        feedback = builder.build_feedback_prompt("原文", "笔记", quality_report)
        assert '65%' in feedback

    def test_feedback_prompt_includes_issues(self):
        """feedback prompt 应包含具体问题描述"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        quality_report = {
            'total_score': 0.5,
            'overall_passed': False,
            'rule_results': {
                'R1_禁止虚构数据': {
                    'issues': [{
                        'severity': 'fatal',
                        'description': '虚构了百分比数据',
                        'suggestion': '删除或标注来源',
                        'line_range': 'L10-L15',
                    }]
                }
            }
        }
        feedback = builder.build_feedback_prompt("原文", "笔记", quality_report)
        assert '虚构' in feedback or 'R1' in feedback

    def test_feedback_prompt_includes_failed_note(self):
        """feedback prompt 应包含上一版笔记"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        quality_report = {'total_score': 0.5, 'overall_passed': False, 'rule_results': {}}
        feedback = builder.build_feedback_prompt("原文", "这是上一版笔记", quality_report)
        assert '上一版笔记' in feedback
        assert '这是上一版笔记' in feedback

    def test_feedback_prompt_short_transcript_included(self):
        """短原文应在 feedback prompt 中包含原文"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        quality_report = {'total_score': 0.5, 'overall_passed': False, 'rule_results': {}}
        short_transcript = "这是一段简短的转写文本"
        feedback = builder.build_feedback_prompt(short_transcript, "笔记", quality_report)
        assert short_transcript in feedback

    def test_feedback_prompt_long_transcript_omitted(self):
        """长原文应在 feedback prompt 中省略"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='lecture')
        quality_report = {'total_score': 0.5, 'overall_passed': False, 'rule_results': {}}
        long_transcript = "字" * 70000  # 粗估 35000 tokens
        feedback = builder.build_feedback_prompt(long_transcript, "笔记", quality_report)
        assert '原文过长' in feedback or '已省略' in feedback


class TestMeetingPrompt:
    """会议纪要专用 prompt 测试"""

    def setup_method(self):
        self.config_dir = Path(__file__).parent.parent / "config"
        self.rules_path = str(self.config_dir / "note_generation_rules.yaml")
        self.experience_path = str(self.config_dir / "experience_log.yaml")
        if not Path(self.rules_path).exists():
            pytest.skip("Config files not found")

    def test_meeting_system_prompt(self):
        """会议纪要 system prompt 应包含会议相关约束"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='meeting')
        system = builder.build_meeting_system_prompt()
        assert '会议' in system
        assert '决策' in system
        assert '行动' in system or '待办' in system

    def test_meeting_user_prompt(self):
        """会议纪要 user prompt 应包含转写文本"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='meeting')
        user = builder.build_meeting_user_prompt("会议转写内容", title="项目周会")
        assert '会议转写内容' in user
        assert '项目周会' in user

    def test_meeting_user_prompt_without_title(self):
        """无标题时会议纪要 user prompt 不应包含会议主题行"""
        from noteforge.core.prompt_builder import PromptBuilder
        builder = PromptBuilder(self.rules_path, self.experience_path, content_type='meeting')
        user = builder.build_meeting_user_prompt("会议转写内容")
        assert '会议主题' not in user


class TestContentTypeConfig:
    """内容类型配置验证测试"""

    def test_valid_content_types(self):
        """应支持所有有效的内容类型"""
        from noteforge.core.prompt_builder import VALID_CONTENT_TYPES
        expected = ['lecture', 'tutorial', 'interview', 'podcast', 'meeting']
        for ct in expected:
            assert ct in VALID_CONTENT_TYPES, f"缺少内容类型: {ct}"

    def test_each_type_has_required_keys(self):
        """每种内容类型配置应包含必要键"""
        from noteforge.core.prompt_builder import CONTENT_TYPE_CONFIG
        required_keys = ['role', 'instruction', 'sections', 'required_sections']
        for ct, cfg in CONTENT_TYPE_CONFIG.items():
            for key in required_keys:
                assert key in cfg, f"内容类型 {ct} 缺少键: {key}"

    def test_required_sections_are_valid_strings(self):
        """required_sections 中的每个条目应为非空字符串"""
        from noteforge.core.prompt_builder import CONTENT_TYPE_CONFIG
        for ct, cfg in CONTENT_TYPE_CONFIG.items():
            for section in cfg.get('required_sections', []):
                assert isinstance(section, str) and len(section) > 0, (
                    f"内容类型 {ct}: required_sections 包含无效条目: {section!r}"
                )
