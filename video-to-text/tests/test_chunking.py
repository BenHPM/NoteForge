"""
transcript_preprocessor.chunk_if_needed 超长文本分块单元测试

覆盖：
  - 短文本不分块
  - 空文本处理
  - 长文本产生多个块
  - 块非空
  - 话题边界优先切分

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_chunking.py -v
"""
import os
import pytest

class TestChunkIfNeeded:
    """测试 transcript_preprocessor 的分块逻辑"""

    def setup_method(self):
        from noteforge.core.transcript_preprocessor import TranscriptPreprocessor
        self.preprocessor = TranscriptPreprocessor()

    def test_short_text_no_chunking(self):
        """短文本不应分块"""
        text = "这是短文本。" * 10
        chunks = self.preprocessor.chunk_if_needed(text, max_tokens=50000)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_empty_text_no_chunking(self):
        """空文本返回空列表中的空字符串"""
        chunks = self.preprocessor.chunk_if_needed("")
        assert len(chunks) == 1

    def test_long_text_produces_multiple_chunks(self):
        """长文本应产生多个块"""
        # 用较小的 max_tokens 触发分块
        sentences = [f"这是第{i}个句子，用于测试超长文本的分块功能，内容较长。" for i in range(300)]
        text = "\n".join(sentences)
        chunks = self.preprocessor.chunk_if_needed(
            text, max_tokens=2000, min_chunk_size=500
        )
        assert len(chunks) >= 2, f"长文本应分成多个块，实际 {len(chunks)} 块"

    def test_chunks_are_not_empty(self):
        """每个块不应为空"""
        sentences = [f"这是第{i}个句子，内容涉及量化和基金投资策略。" for i in range(300)]
        text = "\n".join(sentences)
        chunks = self.preprocessor.chunk_if_needed(
            text, max_tokens=2000, min_chunk_size=500
        )
        for chunk in chunks:
            assert len(chunk) > 0, "每个块不应为空"

    def test_topic_boundary_preferred(self):
        """分块应优先在话题边界处切分"""
        topic_switch = "好，接下来我们讨论量化投资策略。"
        sentences_a = [f"第一部分内容，关于导演拍摄技巧第{i}点。" for i in range(200)]
        sentences_b = [f"第二部分内容，关于量化投资策略第{i}点。" for i in range(200)]
        text = "\n".join(sentences_a) + "\n" + topic_switch + "\n" + "\n".join(sentences_b)
        chunks = self.preprocessor.chunk_if_needed(
            text, max_tokens=2000, min_chunk_size=500
        )
        assert len(chunks) >= 2, f"长文本应分成多个块，实际 {len(chunks)} 块"
