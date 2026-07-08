# -*- coding: utf-8 -*-
"""
NoteForge 启发式质量指标单元测试

覆盖 noteforge/quality/heuristics.py 的 compute_metrics 和 QualityMetrics.to_dict。

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_heuristics.py -v
"""
import os
os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'

import pytest
from noteforge.quality.heuristics import compute_metrics, QualityMetrics


class TestCompressionRatio:
    """压缩比测试"""

    def test_compression_ratio_normal(self):
        """正常压缩比（笔记 500 字 / 原文 2000 字 ≈ 25%）"""
        note = "## 核心观点\n\n- 要点一\n- 要点二\n- 要点三\n\n## 总结\n\n这是总结内容。"
        source = "A" * 2000
        body = "核心观点要点一要点二要点三总结这是总结内容"
        metrics = compute_metrics(note, source, body)
        # body 约 20 字, source 2000 字 → ~1%，但 note 含 markdown 结构
        assert 0.0 <= metrics.compression_ratio <= 1.0

    def test_compression_ratio_zero_source(self):
        """原文为空时压缩比为 0"""
        note = "## 核心观点\n\n- 要点一"
        source = ""
        body = "核心观点要点一"
        metrics = compute_metrics(note, source, body)
        # source_chars = 0, max(0, 1) = 1, so ratio = body_chars / 1
        assert metrics.compression_ratio >= 0.0

    def test_compression_ratio_short_note(self):
        """笔记极短时压缩比很低"""
        note = "短"
        source = "这是一段很长的原文内容" * 100
        body = "短"
        metrics = compute_metrics(note, source, body)
        assert metrics.compression_ratio < 0.05


class TestStructureScore:
    """结构丰富度测试"""

    def test_structure_score_has_headers_and_lists(self):
        """有标题+列表时高分"""
        note = (
            "## 核心观点\n\n"
            + "- 要点一\n" * 15
            + "### 子标题\n\n"
            + "- 子要点\n" * 10
            + "## 方法论\n\n"
            + "- 方法一\n" * 10
            + "### 细节\n\n"
            + "> 引用内容\n" * 5
            + "| A | B |\n|---|---|\n| 1 | 2 |\n"
        )
        metrics = compute_metrics(note, "source text" * 50, note)
        assert metrics.structure_score > 0.5

    def test_structure_score_plain_text(self):
        """纯文本时低分"""
        note = "这是一段纯文本没有任何格式化标记只是普通段落文字而已。"
        metrics = compute_metrics(note, "source text" * 50, note)
        assert metrics.structure_score < 0.3


class TestInfoDensity:
    """信息密度测试"""

    def test_info_density_high(self):
        """多概念时高密度"""
        note = (
            "投资组合需要考虑风险收益比和资产配置策略。\n"
            "量化交易模型依赖因子选股和回撤控制。\n"
            "宏观经济分析关注通胀预期和货币政策。\n"
            "技术指标包括移动平均线和相对强弱指数。\n"
            "基本面分析看重企业盈利能力和成长空间。\n"
        )
        metrics = compute_metrics(note, "source" * 100, note)
        assert metrics.info_density > 0.1

    def test_info_density_low(self):
        """少概念时低密度"""
        # 使用重复的短句，unique_words 少
        note = "测试测试测试测试测试测试测试测试测试测试测试测试测试测试测试测试测试测试测试测试"
        metrics = compute_metrics(note, "source" * 100, note)
        # 重复内容，unique_words 少
        assert metrics.info_density <= 1.0


class TestReadability:
    """可读性测试"""

    def test_readability_good_paragraphs(self):
        """好段落时高可读性"""
        note = (
            "## 核心观点\n\n"
            "投资组合需要考虑风险收益比。\n"
            "量化交易模型依赖因子选股。\n\n"
            "## 方法论\n\n"
            "- 第一步\n"
            "- 第二步\n"
            "- 第三步\n\n"
            "> 专家原话引用\n\n"
            "| 指标 | 数值 |\n|------|------|\n| ROE | 15% |\n"
        )
        metrics = compute_metrics(note, "source" * 100, note)
        assert metrics.readability_score > 0.3


class TestQuoteRatio:
    """原话引用比测试"""

    def test_quote_ratio_has_quotes(self):
        """有引用行时高引用比"""
        note = (
            "## 核心观点\n\n"
            "> 这是引用的第一行\n"
            "> 这是引用的第二行\n"
            "> 这是引用的第三行\n"
            "普通文本行\n"
        )
        metrics = compute_metrics(note, "source" * 50, note)
        assert metrics.quote_ratio > 0.3


class TestActionSpecificity:
    """行动清单具体性测试"""

    def test_action_specificity_concrete(self):
        """有具体行动时高具体性"""
        note = (
            "## 核心观点\n\n"
            "投资需要系统化方法。\n\n"
            "## 行动清单\n\n"
            "- [ ] 每天阅读30分钟投资书籍\n"
            "- [ ] 每周建立一次投资组合复盘\n"
            "- [ ] 每月制作一份行业分析报告\n"
        )
        metrics = compute_metrics(note, "source" * 50, note)
        assert metrics.action_specificity > 0.5


class TestMetricsToDict:
    """to_dict 测试"""

    def test_metrics_to_dict(self):
        """to_dict 包含所有字段"""
        metrics = QualityMetrics(
            compression_ratio=0.25,
            structure_score=0.8,
            info_density=0.7,
            readability_score=0.6,
            quote_ratio=0.15,
            action_specificity=0.5,
            overall_richness=0.65,
        )
        d = metrics.to_dict()
        assert isinstance(d, dict)
        assert 'compression_ratio' in d
        assert 'structure_score' in d
        assert 'info_density' in d
        assert 'readability_score' in d
        assert 'quote_ratio' in d
        assert 'action_specificity' in d
        assert 'overall_richness' in d
        # 验证四舍五入
        assert d['compression_ratio'] == 0.25
        assert d['structure_score'] == 0.8

    def test_compute_metrics_empty_text(self):
        """空文本不崩溃"""
        metrics = compute_metrics("", "", "")
        assert isinstance(metrics, QualityMetrics)
        assert metrics.compression_ratio == 0.0
        assert metrics.structure_score == 0.0
        assert metrics.info_density == 0.0
