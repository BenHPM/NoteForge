# -*- coding: utf-8 -*-
"""模块间交互集成测试（3 tests）。"""
import os
import pytest
from unittest.mock import MagicMock, patch

class TestModuleIntegration:
    """模块间交互测试"""

    def test_generation_result_with_batch_processor(self, tmp_path):
        """GenerationResult 应与 BatchProcessor 正确协作"""
        from noteforge.models import GenerationResult
        from noteforge.batch.processor import BatchProcessor
        from noteforge.context import PathConfig
        notes_dir = tmp_path / "notes"
        transcripts_dir = tmp_path / "transcripts"
        notes_dir.mkdir()
        transcripts_dir.mkdir()
        logger = MagicMock()

        pc = PathConfig(
            base_dir=tmp_path,
            transcripts_dir=transcripts_dir,
            notes_dir=notes_dir,
            reports_dir=tmp_path / "quality_reports",
            logs_dir=tmp_path / "logs",
        )
        bp = BatchProcessor(
            path_config=pc,
            logger=logger,
        )
        results = [
            GenerationResult(transcript_path="t1.txt", total_score=0.9, overall_passed=True,
                           duration_seconds=30, token_usage={'input_tokens': 1000}),
            GenerationResult(transcript_path="t2.txt", total_score=0.5, overall_passed=False,
                           duration_seconds=20, token_usage={'input_tokens': 800}),
            GenerationResult(transcript_path="t3.txt", error="已存在（跳过）",
                           duration_seconds=0, token_usage={}),
        ]
        bp.print_batch_summary(results)

    def test_domain_classifier_with_quality_manager(self, tmp_path):
        """DomainClassifier 与 QualityManager 应独立工作"""
        from noteforge.core.domain_classifier import DomainClassifier
        from noteforge.quality.manager import QualityManager
        from noteforge.context import PathConfig

        domains = [
            {'id': 'test', 'name': '测试', 'match_keywords': ['测试'], 'match_files': []},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        (tmp_path / "reports").mkdir()
        (tmp_path / "notes").mkdir()
        pc = PathConfig(
            base_dir=tmp_path,
            transcripts_dir=tmp_path / "transcripts",
            notes_dir=tmp_path / "notes",
            reports_dir=tmp_path / "reports",
            logs_dir=tmp_path / "logs",
        )
        dc = DomainClassifier(domains=domains, path_config=pc)
        qm = QualityManager(
            path_config=pc,
            logger=MagicMock(),
        )
        assert dc.detect_domain('/path/测试笔记.md') == 'test'
        assert not hasattr(qm, 'get_domain_config')
        assert not hasattr(qm, 'detect_domain')

    def test_audio_handler_title_extraction_independent(self, tmp_path):
        """AudioHandler 的标题提取应独立于转写流程"""
        from noteforge.core.audio_handler import AudioHandler
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        logger = MagicMock()
        handler = AudioHandler(
            transcripts_dir=transcripts_dir,
            base_dir=tmp_path,
            logger=logger,
        )
        title = handler.extract_title(str(tmp_path / "transcripts" / "my_episode.txt"))
        assert title == "my_episode"
