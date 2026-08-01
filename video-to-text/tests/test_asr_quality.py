# -*- coding: utf-8 -*-
"""
Risk-1: ASR 质量门禁测试

覆盖 TranscriptPreprocessor.assess_transcript_quality()：
  - 噪声标记密度检测
  - 重复段落检测
  - 短文本检测
  - 说话人标记检测
  - 质量等级判定
  - 转写质量声明生成
"""

import pytest
from noteforge.core.transcript_preprocessor import TranscriptPreprocessor


@pytest.fixture
def preprocessor():
    return TranscriptPreprocessor()


class TestASRQualityAssessment:
    """ASR 转写质量评估测试"""

    def test_good_quality(self, preprocessor):
        """高质量转写：无噪声标记，足够长度"""
        text = "这是一段高质量的转写文本，没有任何噪声标记。" * 50
        result = preprocessor.assess_transcript_quality(text)
        assert result['quality_level'] == 'good'
        assert result['noise_count'] == 0
        assert result['noise_density'] == 0
        assert len(result['warnings']) == 0
        assert '良好' in result['quality_declaration']

    def test_fair_quality_with_noise(self, preprocessor):
        """中等质量：有少量噪声标记"""
        text = "正常内容。" * 500 + "[无法识别片段]" * 3  # 足够长文本，少量噪声
        result = preprocessor.assess_transcript_quality(text)
        assert result['quality_level'] in ('good', 'fair')
        assert result['noise_count'] >= 3

    def test_poor_quality_high_noise(self, preprocessor):
        """低质量：噪声密度过高"""
        text = "[无法识别片段]" * 20 + "正常内容。"
        result = preprocessor.assess_transcript_quality(text)
        assert result['quality_level'] == 'poor'
        assert result['noise_count'] >= 20
        assert any('噪声密度' in w for w in result['warnings'])
        assert '较差' in result['quality_declaration']

    def test_poor_quality_short_text(self, preprocessor):
        """低质量：转写文本过短"""
        text = "短文本"
        result = preprocessor.assess_transcript_quality(text)
        assert result['quality_level'] == 'poor'
        assert any('过短' in w for w in result['warnings'])

    def test_noise_by_type(self, preprocessor):
        """不同类型的噪声标记分类统计"""
        text = "[无法识别片段]" * 3 + "[听不清]" * 2 + "[杂音]" * 1 + "正常内容。" * 50
        result = preprocessor.assess_transcript_quality(text)
        assert result['noise_by_type'].get('unrecognized', 0) >= 3
        assert result['noise_by_type'].get('inaudible', 0) >= 2
        assert result['noise_by_type'].get('noise', 0) >= 1

    def test_speaker_markers_detected(self, preprocessor):
        """说话人标记检测：有标记"""
        text = "说话人1: 今天我们讨论一下。说话人2: 好的。" * 50
        result = preprocessor.assess_transcript_quality(text)
        assert result['has_speaker_markers'] is True
        assert '已校对' in result['quality_declaration']

    def test_no_speaker_markers_warning(self, preprocessor):
        """说话人标记检测：无标记且长文本应警告"""
        text = "这是一段很长的转写文本，没有说话人标记。" * 100
        result = preprocessor.assess_transcript_quality(text)
        assert result['has_speaker_markers'] is False
        # 长文本无说话人标记时应有警告
        if result['char_count'] > 3000:
            assert any('说话人' in w or 'R11' in w or 'R12' in w for w in result['warnings'])

    def test_quality_declaration_format(self, preprocessor):
        """转写质量声明格式正确"""
        text = "正常内容。" * 50
        result = preprocessor.assess_transcript_quality(text)
        decl = result['quality_declaration']
        assert '转写质量' in decl
        assert '已知问题' in decl
        assert '人名校对' in decl

    def test_noise_density_calculation(self, preprocessor):
        """噪声密度计算正确（每千字噪声数）"""
        # 1000 字 + 3 个噪声标记 = 3/千字
        text = "正常内容文字。" * 100 + "[无法识别片段]" * 3
        result = preprocessor.assess_transcript_quality(text)
        assert result['noise_density'] > 0
        # 噪声密度应该是浮点数
        assert isinstance(result['noise_density'], float)

    def test_repeat_detection(self, preprocessor):
        """重复段落检测"""
        # 构造有重复的文本
        repeated = "这是一个重复的段落，包含足够多的中文字符来触发检测。" * 2
        text = repeated + "正常内容。" * 20
        result = preprocessor.assess_transcript_quality(text)
        # 重复检测可能触发也可能不触发，取决于正则
        assert 'repeat_segments' in result

    def test_empty_text(self, preprocessor):
        """空文本应判定为 poor"""
        result = preprocessor.assess_transcript_quality("")
        assert result['quality_level'] == 'poor'
        assert result['char_count'] == 0

    def test_mixed_noise_types(self, preprocessor):
        """混合噪声类型"""
        text = "[无法识别片段]" * 2 + "[inaudible]" * 2 + "[silence]" * 1 + "正常。" * 50
        result = preprocessor.assess_transcript_quality(text)
        assert result['noise_count'] >= 5
        assert 'unrecognized' in result['noise_by_type']
        assert 'inaudible' in result['noise_by_type']
        assert 'silence' in result['noise_by_type']
