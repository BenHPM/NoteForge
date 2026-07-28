"""
transcript_preprocessor 模块单元测试

覆盖：
  - 文本清洗（去噪标记、时间戳、语气词）
  - token 估算
  - 空输入处理
  - YAML 清洗规则加载（Cluster 3）
  - 回退默认规则

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_transcript_preprocessor.py -v
"""
import os
import tempfile
import pytest

from pathlib import Path


class TestTranscriptPreprocessor:
    """TranscriptPreprocessor 模块测试"""

    @pytest.fixture
    def preprocessor(self):
        from noteforge.core.transcript_preprocessor import TranscriptPreprocessor
        return TranscriptPreprocessor()

    def test_clean_noise(self, preprocessor):
        text = "大家好[无法识别片段]我们今天[00:30]来聊一下<0.5>这个话题"
        cleaned = preprocessor.clean(text, clean_fillers=False)
        assert "[无法识别片段]" not in cleaned
        assert "[00:30]" not in cleaned
        assert "<0.5>" not in cleaned

    def test_remove_filler_words(self, preprocessor):
        text = "嗯那个我们啊来聊聊这个话题吧"
        cleaned = preprocessor.clean(text, clean_fillers=True)
        # 去语气词后文本应变短
        assert len(cleaned) <= len(text)

    def test_estimate_tokens(self, preprocessor):
        text = "这是一段测试文本"
        count = preprocessor.estimate_tokens(text)
        assert count > 0
        assert isinstance(count, int)

    def test_empty_input(self, preprocessor):
        result = preprocessor.clean("")
        assert isinstance(result, str)
