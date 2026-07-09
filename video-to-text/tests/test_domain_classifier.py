# -*- coding: utf-8 -*-
"""DomainClassifier 知识域分类器单元测试（14 tests）。"""
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'


class TestDomainClassifier:
    """DomainClassifier 知识域分类器测试"""

    def _make_classifier(self, domains):
        from noteforge.core.domain_classifier import DomainClassifier
        from noteforge.context import PathConfig
        pc = PathConfig(
            base_dir=Path('.'),
            transcripts_dir=Path('.'),
            notes_dir=Path('.'),
            reports_dir=Path('.'),
            logs_dir=Path('.'),
        )
        return DomainClassifier(domains=domains, path_config=pc)

    def test_match_files_priority(self):
        """match_files 应优先于关键词匹配"""
        domains = [
            {'id': 'test_domain', 'name': '测试域', 'match_files': ['ep01*'], 'match_keywords': []},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        assert dc.detect_domain('/some/path/ep01-intro.md') == 'test_domain'

    def test_fallback_to_general(self):
        """无匹配时应归入 general"""
        domains = [
            {'id': 'finance', 'name': '金融', 'match_files': ['*量化*'], 'match_keywords': ['量化']},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        assert dc.detect_domain('/some/path/random_note.md') == 'general'

    def test_exclude_keywords(self):
        """排除词应阻止匹配"""
        domains = [
            {'id': 'finance', 'name': '金融', 'match_keywords': ['投资'], 'exclude_keywords': ['导演']},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        assert dc.detect_domain('/some/path/导演投资课.md') == 'general'

    def test_exclude_keywords_in_content(self):
        """排除词在内容中也应阻止匹配"""
        domains = [
            {'id': 'finance', 'name': '金融', 'match_keywords': ['投资'], 'exclude_keywords': ['导演']},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        with patch('noteforge.core.domain_classifier.read_file', return_value='导演讲投资策略'):
            assert dc.detect_domain('/some/path/投资课.md') == 'general'

    def test_get_domain_config_found(self):
        """get_domain_config 应返回正确域配置"""
        domains = [
            {'id': 'test', 'name': '测试', 'match_keywords': [], 'match_files': []},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        assert dc.get_domain_config('test')['name'] == '测试'

    def test_get_domain_config_fallback(self):
        """get_domain_config 对不存在的 ID 应回退到 general"""
        domains = [
            {'id': 'test', 'name': '测试', 'match_keywords': [], 'match_files': []},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        result = dc.get_domain_config('nonexistent')
        assert result['id'] == 'general'

    def test_validate_domain_match_same_domain(self):
        """同一域的笔记和合成文档应匹配"""
        domains = [
            {'id': 'finance', 'name': '金融', 'match_keywords': ['投资'], 'output_name': '金融-知识体系'},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        is_match, note_dom, synth_dom = dc.validate_domain_match(
            '/path/投资笔记.md', '/path/金融-知识体系.md')
        assert is_match is True
        assert note_dom == 'finance'

    def test_validate_domain_match_different_domain(self):
        """不同域的笔记和合成文档应不匹配"""
        domains = [
            {'id': 'finance', 'name': '金融', 'match_keywords': ['投资'], 'output_name': '金融-知识体系'},
            {'id': 'directing', 'name': '导演', 'match_keywords': ['导演'], 'output_name': '导演-知识体系'},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        is_match, note_dom, synth_dom = dc.validate_domain_match(
            '/path/投资笔记.md', '/path/导演-知识体系.md')
        assert is_match is False
        assert note_dom == 'finance'
        assert synth_dom == 'directing'

    def test_empty_domains_returns_general(self):
        """空域列表应返回 general"""
        dc = self._make_classifier([])
        assert dc.detect_domain('/some/path/note.md') == 'general'

    def test_keyword_weighting_title_heavier(self):
        """标题匹配应比内容匹配权重高"""
        domains = [
            {'id': 'finance', 'name': '金融', 'match_keywords': ['量化', '投资']},
            {'id': 'directing', 'name': '导演', 'match_keywords': ['导演', '拍摄']},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        with patch('noteforge.core.domain_classifier.read_file', return_value='内容无关'):
            assert dc.detect_domain('/path/量化交易入门.md') == 'finance'

    def test_get_notes_by_domain_groups_correctly(self):
        """get_notes_by_domain 应正确分组"""
        domains = [
            {'id': 'test_domain', 'name': '测试', 'match_files': ['ep01*'], 'match_keywords': []},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        groups = dc.get_notes_by_domain(note_paths=[
            '/path/ep01-intro.md',
            '/path/other_note.md',
        ])
        assert 'test_domain' in groups
        assert '/path/ep01-intro.md' in groups['test_domain']
        assert 'general' in groups
        assert '/path/other_note.md' in groups['general']

    def test_corrections_highest_priority(self):
        """修正记录应具有最高优先级"""
        domains = [
            {'id': 'test_domain', 'name': '测试', 'match_files': ['ep01*'], 'match_keywords': []},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        with patch.object(dc, '_load_corrections', return_value={'ep01-intro': 'general'}):
            assert dc.detect_domain('/path/ep01-intro.md') == 'general'

    def test_match_files_fnmatch_pattern(self):
        """match_files 应支持 fnmatch 通配符"""
        domains = [
            {'id': 'finance', 'name': '金融', 'match_files': ['*量化*'], 'match_keywords': []},
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        assert dc.detect_domain('/path/深度量化投资.md') == 'finance'
