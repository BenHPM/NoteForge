# -*- coding: utf-8 -*-
"""
NoteForge 知识域分类器
加权分类：文件名匹配（优先）→ 标题+内容关键词加权
"""

import logging
from fnmatch import fnmatch
from pathlib import Path
from typing import List, Optional, Dict

import yaml


class DomainClassifier:
    """知识域分类器（自包含，无 LLMNoteEngine 依赖）"""

    _TITLE_WEIGHT = 0.4
    _CONTENT_WEIGHT = 0.6

    def __init__(self, domains: list, base_dir: Path, notes_dir: Path):
        """
        Args:
            domains: knowledge_domains 配置列表
            base_dir: 项目根目录（用于查找 config/）
            notes_dir: 笔记输出目录（用于 get_notes_by_domain 扫描）
        """
        self._domains = domains
        self._base_dir = base_dir
        self._notes_dir = notes_dir
        self.logger = logging.getLogger('noteforge.domain')

        # 分类修正记录缓存
        self._corrections_cache: Optional[dict] = None
        self._corrections_mtime: float = 0.0

    # ----------------------------------------------------------
    # 公开接口
    # ----------------------------------------------------------

    def detect_domain(self, note_path: str) -> str:
        """
        加权分类：文件名匹配（优先）→ 标题+内容关键词加权
        优先检查修正记录（用户手动修正的分类）
        """
        if not self._domains:
            return 'general'

        stem = Path(note_path).stem

        # 0. 检查修正记录（最高优先级）
        corrections = self._load_corrections()
        if stem in corrections:
            return corrections[stem]

        # 1. 文件名匹配（优先级高于关键词，与 config 注释一致）
        for domain in self._domains:
            if domain['id'] == 'general':
                continue
            match_files = domain.get('match_files', [])
            if match_files and any(fnmatch(stem, pat) for pat in match_files):
                return domain['id']

        # 2. 关键词加权匹配
        try:
            content = self._read_file(note_path)
            content_lower = content[:5000].lower()
        except Exception:
            content_lower = ""

        title_lower = stem.lower()
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
                if any(kw in title_lower for kw in excludes):
                    continue
                if any(kw in content_lower for kw in excludes):
                    continue
            # 统计命中数
            title_hits = sum(1 for kw in keywords if kw in title_lower)
            content_hits = sum(1 for kw in keywords if kw in content_lower)
            # 归一化后加权
            total_kw = max(len(keywords), 1)
            combined = (title_hits / total_kw) * self._TITLE_WEIGHT + \
                       (content_hits / total_kw) * self._CONTENT_WEIGHT
            if combined > best_score:
                best_score = combined
                best_domain = domain['id']

        return best_domain

    def get_domain_config(self, domain_id: str) -> dict:
        """获取指定域的配置"""
        for d in self._domains:
            if d['id'] == domain_id:
                return d
        return {'id': 'general', 'name': '其他', 'output_name': '其他笔记-知识体系'}

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
    # 内部方法
    # ----------------------------------------------------------

    def _load_corrections(self) -> dict:
        """加载分类修正记录（带文件 mtime 缓存，避免批量操作时反复读文件）"""
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

    @staticmethod
    def _read_file(path: str) -> str:
        """读取文件（尝试 UTF-8，回退 GBK）"""
        for encoding in ('utf-8', 'gbk', 'gb2312'):
            try:
                with open(path, 'r', encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法读取文件（编码问题）: {path}")
