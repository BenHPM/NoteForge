"""
DomainClassifier 知识域分类单元测试

覆盖：
  - match_files 优先于关键词匹配
  - 无匹配时归入 general

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_domain_detection.py -v
"""
import os
import pytest
from pathlib import Path

os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


class TestDetectDomain:
    """测试 DomainClassifier 域分类逻辑"""

    def setup_method(self):
        """使用测试配置构造分类器"""
        # 通过设置环境变量跳过 env_check
        os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'

    def test_match_files_priority(self):
        """match_files 应优先于关键词匹配"""
        from noteforge.core.domain_classifier import DomainClassifier
        from noteforge.context import PathConfig
        domains = [
            {
                'id': 'test_domain',
                'name': '测试域',
                'match_files': ['ep01*'],
                'match_keywords': [],
            },
            {
                'id': 'general',
                'name': '其他',
                'match_keywords': [],
                'match_files': [],
            },
        ]
        pc = PathConfig(
            base_dir=Path('.'), transcripts_dir=Path('.'), notes_dir=Path('.'),
            reports_dir=Path('.'), logs_dir=Path('.'),
        )
        classifier = DomainClassifier(domains=domains, path_config=pc)
        # ep01 开头的文件应匹配 test_domain
        result = classifier.detect_domain('/some/path/ep01-intro.md')
        assert result == 'test_domain', f"文件名匹配应优先: {result}"

    def test_fallback_to_general(self):
        """无匹配时应归入 general"""
        from noteforge.core.domain_classifier import DomainClassifier
        from noteforge.context import PathConfig
        domains = [
            {'id': 'finance', 'name': '金融', 'match_files': ['*量化*'], 'match_keywords': ['量化']},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        pc = PathConfig(
            base_dir=Path('.'), transcripts_dir=Path('.'), notes_dir=Path('.'),
            reports_dir=Path('.'), logs_dir=Path('.'),
        )
        classifier = DomainClassifier(domains=domains, path_config=pc)
        result = classifier.detect_domain('/some/path/random_note.md')
        assert result == 'general', f"无匹配应归入 general: {result}"
