"""
PromptBuilder.build_user_prompt 单元测试

覆盖：
  - 接受 mode 参数
  - 不传 title 正常工作
  - 不同 content_type 产生不同指令

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_prompt_building.py -v
"""
import os
import pytest
from pathlib import Path

os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


class TestBuildUserPrompt:
    """测试 prompt_builder.build_user_prompt"""

    def setup_method(self):
        os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'
        # 需要有效的 YAML 配置文件
        self.config_dir = Path(__file__).parent.parent / "config"

    def test_accepts_mode_parameter(self):
        """build_user_prompt 应接受 mode 参数"""
        from noteforge.core.prompt_builder import PromptBuilder
        rules_path = str(self.config_dir / "note_generation_rules.yaml")
        experience_path = str(self.config_dir / "experience_log.yaml")
        if not Path(rules_path).exists():
            pytest.skip("note_generation_rules.yaml not found")
        builder = PromptBuilder(rules_path, experience_path, content_type='lecture')
        # 不应抛出 TypeError
        result = builder.build_user_prompt("转写文本", title="标题", mode='notes')
        assert isinstance(result, str)
        assert len(result) > 0

    def test_without_title(self):
        """不传 title 也应正常工作"""
        from noteforge.core.prompt_builder import PromptBuilder
        rules_path = str(self.config_dir / "note_generation_rules.yaml")
        experience_path = str(self.config_dir / "experience_log.yaml")
        if not Path(rules_path).exists():
            pytest.skip("note_generation_rules.yaml not found")
        builder = PromptBuilder(rules_path, experience_path, content_type='lecture')
        result = builder.build_user_prompt("转写文本")
        assert isinstance(result, str)

    def test_content_type_affects_instruction(self):
        """不同 content_type 应产生不同指令"""
        from noteforge.core.prompt_builder import PromptBuilder
        rules_path = str(self.config_dir / "note_generation_rules.yaml")
        experience_path = str(self.config_dir / "experience_log.yaml")
        if not Path(rules_path).exists():
            pytest.skip("note_generation_rules.yaml not found")
        builder_lecture = PromptBuilder(rules_path, experience_path, content_type='lecture')
        builder_podcast = PromptBuilder(rules_path, experience_path, content_type='podcast')
        result_lecture = builder_lecture.build_user_prompt("转写文本")
        result_podcast = builder_podcast.build_user_prompt("转写文本")
        # lecture 和 podcast 的指令应该不同
        assert result_lecture != result_podcast, "不同 content_type 应产生不同指令"
