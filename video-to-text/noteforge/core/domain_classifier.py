# -*- coding: utf-8 -*-
"""
NoteForge 知识域分类器
加权分类：文件名匹配（优先）→ 标题+内容关键词加权 → TF-IDF 余弦相似度兜底
"""

import logging
import math
import re
from collections import Counter
from fnmatch import fnmatch
from pathlib import Path
from typing import List, Optional, Dict

import yaml

from noteforge.infra.file_io import read_file


class DomainClassifier:
    """知识域分类器（自包含，无 LLMNoteEngine 依赖）"""

    _TITLE_WEIGHT = 0.4
    _CONTENT_WEIGHT = 0.6

    def __init__(self, domains: list, path_config=None, base_dir: Path = None, notes_dir: Path = None):
        """
        Args:
            domains: knowledge_domains 配置列表
            path_config: PathConfig 共享路径配置（可为 None，仅使用 domains）
            base_dir: 项目根目录（已废弃，优先使用 path_config.base_dir）
            notes_dir: 笔记输出目录（已废弃，优先使用 path_config.notes_dir）
        """
        self._domains = domains
        # path_config 可为 None（如仅用于域检测，不涉及文件操作）
        if path_config is not None:
            self._path_config = path_config
        elif base_dir is not None and notes_dir is not None:
            self._path_config = None  # 已废弃路径属性
        else:
            self._path_config = None
        self.logger = logging.getLogger('noteforge.domain')

        # 分类修正记录缓存
        self._corrections_cache: Optional[dict] = None
        self._corrections_mtime: float = 0.0

        # TF-IDF 兜底配置（可从 YAML 配置加载）
        self._use_tfidf_fallback: bool = True
        self._fallback_threshold: float = 0.15
        self._tie_threshold: float = 0.01

    # 兼容属性（委托到 _path_config）
    @property
    def _base_dir(self):
        if self._path_config is None:
            return None
        return self._path_config.base_dir

    @property
    def _notes_dir(self):
        if self._path_config is None:
            return None
        return self._path_config.notes_dir

    # ----------------------------------------------------------
    # 公开接口
    # ----------------------------------------------------------

    def detect_domain(self, note_path: str, use_tfidf_fallback: bool = True) -> str:
        """
        加权分类：文件名精确匹配（优先）→ 标题+内容关键词加权 → TF-IDF 余弦相似度兜底
        优先检查修正记录（用户手动修正的分类）

        Args:
            note_path: 笔记文件路径
            use_tfidf_fallback: 是否在关键词分数低或平局时启用 TF-IDF 兜底

        Returns:
            知识域 ID
        """
        if not self._domains:
            return 'general'

        stem = Path(note_path).stem
        stem_lower = stem.lower()

        # 0. 检查修正记录（最高优先级）
        corrections = self._load_corrections()
        if stem in corrections:
            return corrections[stem]

        # 1. 文件名模式匹配（match_files）
        for domain in self._domains:
            if domain['id'] == 'general':
                continue
            match_files = domain.get('match_files', [])
            if match_files and any(fnmatch(stem, pat) for pat in match_files):
                return domain['id']

        # 2. 关键词加权匹配（文件名关键词 + 内容关键词）
        try:
            content = read_file(note_path)
            content_lower = content[:5000].lower()
        except Exception:
            content_lower = ""

        best_domain = 'general'
        best_score = 0

        for domain in self._domains:
            if domain['id'] == 'general':
                continue
            keywords = domain.get('match_keywords', [])
            if not keywords:
                continue
            excludes = domain.get('exclude_keywords', [])
            # 排除词检查（标题和内容都检查）
            if excludes:
                if any(kw.lower() in stem_lower for kw in excludes):
                    continue
                if any(kw.lower() in content_lower for kw in excludes):
                    continue
            # 统计命中数
            title_hits = sum(1 for kw in keywords if kw.lower() in stem_lower)
            content_hits = sum(1 for kw in keywords if kw.lower() in content_lower)
            # 归一化后加权
            total_kw = max(len(keywords), 1)
            combined = (title_hits / total_kw) * self._TITLE_WEIGHT + \
                       (content_hits / total_kw) * self._CONTENT_WEIGHT
            if combined > best_score:
                best_score = combined
                best_domain = domain['id']

        # 3. TF-IDF 兜底：关键词分数低或有平局时启用
        if use_tfidf_fallback and self._use_tfidf_fallback and best_domain != 'general':
            need_fallback = (
                best_score < self._fallback_threshold
                or self._has_near_tie(best_score, content_lower, stem_lower)
            )
            if need_fallback:
                try:
                    full_content = read_file(note_path)
                    tfidf_result = self._tfidf_fallback(note_path, full_content)
                    if tfidf_result:
                        self.logger.debug(
                            "TF-IDF 兜底激活 (keyword_score=%.3f): %s -> %s",
                            best_score, best_domain, tfidf_result
                        )
                        return tfidf_result
                except Exception as e:
                    self.logger.debug("TF-IDF 兜底失败，保留关键词结果: %s", e)

        return best_domain

    def get_domain_config(self, domain_id: str) -> dict:
        """获取指定域的配置"""
        for d in self._domains:
            if d['id'] == domain_id:
                return d
        return {'id': 'general', 'name': '其他', 'output_name': '其他笔记-知识体系'}

    def classify_text(self, text: str) -> str:
        """
        对任意文本（飞书分类名/标题）做纯关键词域匹配（不读文件，不排除）。

        单一入口：auto_pipeline 的飞书分类→域映射第三层。
        注意：不使用 exclude_keywords（那是为笔记内容设计的，不适用于分类名）。
        """
        if not text or not self._domains:
            return 'general'
        text_lower = text.lower()
        best_domain = 'general'
        best_score = 0.0
        for domain in self._domains:
            if domain['id'] == 'general':
                continue
            keywords = domain.get('match_keywords', [])
            if not keywords:
                continue
            hits = sum(1 for kw in keywords if kw.lower() in text_lower)
            if hits == 0:
                continue
            # 归一化命中占比，避免大关键词列表域占优
            score = hits / len(keywords)
            if score > best_score:
                best_score = score
                best_domain = domain['id']
        return best_domain

    def get_notes_by_domain(self, note_paths: List[str] = None) -> Dict[str, List[str]]:
        """
        将笔记按知识域分组

        Returns:
            {domain_id: [note_path, ...]}
        """
        if note_paths is None:
            note_paths = sorted(str(p) for p in self._notes_dir.glob('*.md'))
            note_paths = [p for p in note_paths
                          if not Path(p).stem.startswith(('knowledge_',
                                                           'mental_models',
                                                           'action_playbook',
                                                           'extraction_',
                                                           'contradictions_'))]

        groups: Dict[str, List[str]] = {}
        for path in note_paths:
            domain = self.detect_domain(path)
            groups.setdefault(domain, []).append(path)

        return groups

    def validate_domain_match(self, note_path: str,
                               synthesis_path: str) -> tuple:
        """
        验证笔记与合成文档是否属于同一知识域

        Returns:
            (is_match: bool, note_domain: str, synthesis_domain: str)
        """
        note_domain = self.detect_domain(note_path)

        # 从合成文档的文件名或内容推断其域
        synthesis_stem = Path(synthesis_path).stem
        synthesis_domain = 'general'
        for domain in self._domains:
            output_name = domain.get('output_name', '')
            if output_name and output_name in synthesis_stem:
                synthesis_domain = domain['id']
                break

        return (note_domain == synthesis_domain, note_domain, synthesis_domain)

    # ----------------------------------------------------------
    # TF-IDF 兜底
    # ----------------------------------------------------------

    def _has_near_tie(self, best_score: float, content_lower: str,
                      stem_lower: str) -> bool:
        """检查是否存在近平局（两个及以上域分数非常接近）"""
        scores = []
        for domain in self._domains:
            if domain['id'] == 'general':
                continue
            keywords = domain.get('match_keywords', [])
            if not keywords:
                continue
            excludes = domain.get('exclude_keywords', [])
            if excludes:
                if any(kw.lower() in stem_lower for kw in excludes):
                    continue
                if any(kw.lower() in content_lower for kw in excludes):
                    continue
            title_hits = sum(1 for kw in keywords if kw.lower() in stem_lower)
            content_hits = sum(1 for kw in keywords if kw.lower() in content_lower)
            total_kw = max(len(keywords), 1)
            combined = (title_hits / total_kw) * self._TITLE_WEIGHT + \
                       (content_hits / total_kw) * self._CONTENT_WEIGHT
            scores.append(combined)

        if len(scores) < 2:
            return False
        scores.sort(reverse=True)
        # 前两名分数差在 tie_threshold 以内即为平局
        return (scores[0] - scores[1]) <= self._tie_threshold

    def _tfidf_fallback(self, note_path: str, note_content: str) -> Optional[str]:
        """
        TF-IDF 余弦相似度兜底分类

        Args:
            note_path: 笔记路径
            note_content: 笔记完整内容

        Returns:
            最佳匹配域 ID，或 None（无法判断时保留原始结果）
        """
        # 构建查询向量（note 的标题 + 前 2000 字符）
        try:
            stem = Path(note_path).stem
            query_text = stem + " " + note_content[:2000]
        except Exception:
            return None

        query_vec = self._build_tf_vector(self._tokenize(query_text))
        if not query_vec:
            return None

        best_domain = None
        best_similarity = -1.0

        for domain in self._domains:
            if domain['id'] == 'general':
                continue
            keywords = domain.get('match_keywords', [])
            if not keywords:
                continue
            # 域的"文档" = 关键词拼接
            domain_doc = " ".join(keywords)
            domain_vec = self._build_tf_vector(self._tokenize(domain_doc))
            if not domain_vec:
                continue

            similarity = self._cosine_similarity(query_vec, domain_vec)
            self.logger.debug(
                "TF-IDF 相似度 %s: %.4f", domain['id'], similarity
            )
            if similarity > best_similarity:
                best_similarity = similarity
                best_domain = domain['id']

        return best_domain

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """
        中文分词（与 KnowledgeIndex._tokenize 保持一致的逻辑）

        Args:
            text: 原始文本

        Returns:
            分词结果列表（过滤停用词、短词、纯数字、纯符号）
        """
        import jieba
        # 清理 Markdown 语法
        clean = re.sub(r'[#*|\-\[\]()>`~]', '', text)
        clean = re.sub(r'https?://\S+', '', clean)
        words = jieba.lcut(clean)
        # 过滤停用词和短词
        stop_words = DomainClassifier._STOP_WORDS
        return [w for w in words
                if w not in stop_words
                and len(w) >= 2
                and not w.isdigit()
                and not re.match(r'^[\W\s]+$', w)]

    # 中文停用词（高频无意义词）— 与 KnowledgeIndex 保持一致
    _STOP_WORDS = set([
        '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一',
        '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有',
        '看', '好', '自己', '这', '他', '她', '它', '们', '那', '里', '为', '什么',
        '把', '让', '被', '从', '对', '但', '而', '如果', '因为', '所以', '这个',
        '那个', '还', '能', '可以', '已经', '或者', '而且', '但是', '虽然', '不过',
        '就是', '然后', '其实', '觉得', '知道', '时候', '来说', '比较', '非常',
        '还是', '应该', '可能', '需要', '通过', '以及', '这样', '那样', '那么',
        '一些', '一下', '一定', '一些', '一种', '每个', '其中', '进行', '使用',
        '原文', '段落', '笔记', '整理', '时间', '来源', '视频', '音频', '转写',
        '问题', '方法', '系统', '方式', '过程', '情况', '方面', '内容',
        '部分', '东西', '事情', '地方', '样子', '道理', '感觉', '状态',
        '出来', '起来', '下来', '上去', '过来', '过去', '回来',
        '第一', '第二', '第三', '第四', '第五', '第六', '第七', '第八',
    ])

    @staticmethod
    def _build_tf_vector(tokens: List[str]) -> Dict[str, float]:
        """
        构建词频向量（Term Frequency）

        Args:
            tokens: 分词结果

        Returns:
            {词: 频率} 字典
        """
        if not tokens:
            return {}
        counter = Counter(tokens)
        total = len(tokens)
        return {word: count / total for word, count in counter.items()}

    @staticmethod
    def _cosine_similarity(vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
        """
        计算两个 TF 向量的余弦相似度

        Args:
            vec_a: 向量 A
            vec_b: 向量 B

        Returns:
            余弦相似度（0.0 ~ 1.0）
        """
        # 计算点积（仅计算共有的词）
        common_keys = set(vec_a.keys()) & set(vec_b.keys())
        dot_product = sum(vec_a[k] * vec_b[k] for k in common_keys)
        # 计算模长
        norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
        norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot_product / (norm_a * norm_b)

    def _load_corrections(self) -> dict:
        """加载分类修正记录（带文件 mtime 缓存，避免批量操作时反复读文件）"""
        if self._base_dir is None:
            return {}
        corrections_path = self._base_dir / 'config' / 'classification_corrections.yaml'
        if not corrections_path.exists():
            return {}
        try:
            mtime = corrections_path.stat().st_mtime
            if self._corrections_cache is not None and mtime == self._corrections_mtime:
                return self._corrections_cache
            with open(corrections_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            result = data.get('corrections', {}) or {}
            self._corrections_cache = result
            self._corrections_mtime = mtime
            return result
        except Exception as e:
            self.logger.debug(f"分类修正记录加载失败: {e}")
            return {}

