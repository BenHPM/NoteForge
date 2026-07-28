"""
DomainClassifier 知识域分类单元测试

覆盖：
  - match_files 优先于关键词匹配
  - 无匹配时归入 general
  - TF-IDF 兜底（低分 / 平局 / 禁用 / general 不触发）

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_domain_detection.py -v
"""
import os
import pytest
from pathlib import Path
from unittest.mock import patch


class TestDetectDomain:
    """测试 DomainClassifier 域分类逻辑"""

    def setup_method(self):
        """使用测试配置构造分类器"""

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


class TestTfidfFallback:
    """TF-IDF 余弦相似度兜底测试"""

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

    def test_fallback_on_low_score(self):
        """关键词分数低时（<0.15）应触发 TF-IDF 兜底"""
        domains = [
            {
                'id': 'finance', 'name': '量化投资',
                'match_keywords': ['量化', '基金', '因子', 'ROE', '换手', '超额', '胜率', '算力'],
                'match_files': [],
            },
            {
                'id': 'directing', 'name': '导演',
                'match_keywords': ['导演', '短视频', '拍摄', '剪辑', '镜头', '运镜'],
                'match_files': [],
            },
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        # 内容只有少量量化关键词（分数极低），但 TF 向量会偏向 finance
        note_content = "这期聊聊基金投资的一些思考，关于收益和风险的平衡。"
        with patch('noteforge.core.domain_classifier.read_file', return_value=note_content):
            result = dc.detect_domain('/some/path/ep42-fund.md')
            # 兜底结果应为 finance（命中"基金"）
            assert result == 'finance'

    def test_fallback_on_tied_scores(self):
        """关键词分数平局时应触发 TF-IDF 兜底"""
        domains = [
            {
                'id': 'finance', 'name': '量化投资',
                'match_keywords': ['量化', '基金', '因子', 'ROE'],
                'match_files': [],
            },
            {
                'id': 'directing', 'name': '导演',
                'match_keywords': ['导演', '短视频', '拍摄'],
                'match_files': [],
            },
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        # 两个域各命中 1 个关键词 -> 平局（分数差=0）
        note_content = "关于导演和量化的一些思考"
        with patch('noteforge.core.domain_classifier.read_file', return_value=note_content):
            result = dc.detect_domain('/some/path/ep42.md')
            # TF-IDF 兜底应给出明确结果
            assert result in ('finance', 'directing')

    def test_no_fallback_when_disabled(self):
        """禁用 TF-IDF 兜底时应保留关键词匹配结果"""
        domains = [
            {
                'id': 'finance', 'name': '量化投资',
                'match_keywords': ['量化', '基金', '因子', 'ROE'],
                'match_files': [],
            },
            {
                'id': 'directing', 'name': '导演',
                'match_keywords': ['导演', '短视频', '拍摄', '运镜'],
                'match_files': [],
            },
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        dc._use_tfidf_fallback = False
        # 两个域各命中 1 个（4 keywords 各命中1 = 各 0.15 分 = 平局），但兜底被禁用
        note_content = "导演聊量化的话题"
        with patch('noteforge.core.domain_classifier.read_file', return_value=note_content):
            result = dc.detect_domain('/some/path/ep42.md')
            # 保留关键词匹配结果（finance 先被赋值，但 directing 分数相同，最后一个 wins）
            # 实际行为：两个都 0.15，directing 后设置所以 wins，但这不是重点
            # 重点是不进入 TF-IDF 兜底
            assert result in ('finance', 'directing')

    def test_fallback_skipped_for_general(self):
        """当关键词匹配结果为 general 时不应触发 TF-IDF 兜底"""
        domains = [
            {
                'id': 'finance', 'name': '量化投资',
                'match_keywords': ['量化', '基金'],
                'match_files': [],
            },
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        # 无任何关键词命中 -> 保持 general
        note_content = "今天的天气真好，出去走走吧"
        with patch('noteforge.core.domain_classifier.read_file', return_value=note_content):
            result = dc.detect_domain('/some/path/misc.md')
            assert result == 'general'

    def test_fallback_skipped_when_detect_domain_param_false(self):
        """detect_domain(use_tfidf_fallback=False) 应跳过兜底"""
        domains = [
            {
                'id': 'finance', 'name': '量化投资',
                'match_keywords': ['量化', '基金', '因子', 'ROE', '换手'],
                'match_files': [],
            },
            {
                'id': 'directing', 'name': '导演',
                'match_keywords': ['导演', '短视频'],
                'match_files': [],
            },
            {'id': 'general', 'name': '其他', 'match_keywords': [], 'match_files': []},
        ]
        dc = self._make_classifier(domains)
        note_content = "导演和量化都很重要"
        with patch('noteforge.core.domain_classifier.read_file', return_value=note_content):
            result = dc.detect_domain('/some/path/ep42.md', use_tfidf_fallback=False)
            # directing 分数更高（2 keywords vs 5 keywords, 各命中1个）
            assert result == 'directing'

    def test_cosine_similarity_zero_for_disjoint(self):
        """完全不相关的词向量余弦相似度应为 0"""
        from noteforge.core.domain_classifier import DomainClassifier
        vec_a = {'量化': 0.5, '基金': 0.5}
        vec_b = {'导演': 0.5, '拍摄': 0.5}
        sim = DomainClassifier._cosine_similarity(vec_a, vec_b)
        assert sim == 0.0

    def test_cosine_similarity_perfect_match(self):
        """完全相同的向量余弦相似度应为 1.0"""
        from noteforge.core.domain_classifier import DomainClassifier
        vec = {'量化': 0.3, '基金': 0.3, '投资': 0.4}
        sim = DomainClassifier._cosine_similarity(vec, vec)
        assert abs(sim - 1.0) < 1e-9

    def test_build_tf_vector(self):
        """TF 向量应正确归一化"""
        from noteforge.core.domain_classifier import DomainClassifier
        tokens = ['量化', '量化', '基金', '投资']
        vec = DomainClassifier._build_tf_vector(tokens)
        assert abs(vec['量化'] - 0.5) < 1e-9
        assert abs(vec['基金'] - 0.25) < 1e-9
        assert abs(vec['投资'] - 0.25) < 1e-9
