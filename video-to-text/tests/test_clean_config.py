"""
transcript_preprocessor.clean() 配置参数单元测试

覆盖：
  - clean_unrecognized 开关（[无法识别片段]）
  - clean_timestamps 开关（[HH:MM:SS]）
  - clean_fillers 开关（语气词）
  - 全关闭保留所有噪声

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_clean_config.py -v
"""
import os
import pytest

os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


class TestCleanConfig:
    """测试 transcript_preprocessor.clean() 的配置开关"""

    def setup_method(self):
        from noteforge.core.transcript_preprocessor import TranscriptPreprocessor
        self.preprocessor = TranscriptPreprocessor()

    def test_clean_unrecognized_enabled(self):
        """开启清理时应移除 [无法识别片段]"""
        text = "这是正文[无法识别片段]后续内容"
        result = self.preprocessor.clean(text, clean_unrecognized=True)
        assert "[无法识别片段]" not in result
        assert "后续内容" in result

    def test_clean_unrecognized_disabled(self):
        """关闭清理时应保留 [无法识别片段]"""
        text = "这是正文[无法识别片段]后续内容"
        result = self.preprocessor.clean(text, clean_unrecognized=False)
        assert "[无法识别片段]" in result

    def test_clean_timestamps_enabled(self):
        """开启清理时应移除 [HH:MM:SS] 时间戳"""
        text = "[00:01:23]这是正文"
        result = self.preprocessor.clean(text, clean_timestamps=True)
        assert "[00:01:23]" not in result
        assert "这是正文" in result

    def test_clean_timestamps_disabled(self):
        """关闭清理时应保留时间戳"""
        text = "[00:01:23]这是正文"
        result = self.preprocessor.clean(text, clean_timestamps=False)
        assert "[00:01:23]" in result

    def test_all_cleaning_disabled(self):
        """所有清理都关闭时应保留噪声"""
        text = "[无法识别片段][00:01:23]嗯正文"
        result = self.preprocessor.clean(
            text, clean_fillers=False, clean_unrecognized=False, clean_timestamps=False
        )
        assert "[无法识别片段]" in result
        assert "[00:01:23]" in result
        assert "嗯" in result
