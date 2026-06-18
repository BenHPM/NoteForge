"""
NoteForge 知识索引模块 v1.0
功能:
- 索引 output/notes/ 下所有 .md 笔记
- 全文搜索（关键词 + 正则）
- 自动标签提取（jieba 分词 + TF-IDF）
- 笔记关联（基于关键词相似度）
"""

import os
import re
import math
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from collections import Counter


@dataclass
class NoteSummary:
    """笔记摘要"""
    path: str
    title: str
    date: str
    tags: List[str]
    key_frameworks: List[str]
    action_items: List[str]
    char_count: int
    word_count: int


@dataclass
class SearchResult:
    """搜索结果"""
    path: str
    title: str
    relevance: float  # 0.0 ~ 1.0
    snippet: str       # 匹配片段
    tags: List[str]
    date: str


class KnowledgeIndex:
    """笔记知识索引"""

    # 中文停用词（高频无意义词）
    STOP_WORDS = set([
        '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一',
        '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有',
        '看', '好', '自己', '这', '他', '她', '它', '们', '那', '里', '为', '什么',
        '把', '让', '被', '从', '对', '但', '而', '如果', '因为', '所以', '这个',
        '那个', '还', '能', '可以', '已经', '或者', '而且', '但是', '虽然', '不过',
        '就是', '然后', '其实', '觉得', '知道', '时候', '来说', '比较', '非常',
        '还是', '应该', '可能', '需要', '通过', '以及', '这样', '那样', '那么',
        '一些', '一下', '一定', '一些', '一种', '每个', '其中', '进行', '使用',
        '原文', '段落', '笔记', '整理', '时间', '来源', '视频', '音频', '转写',
        # 扩充停用词（高频但低信息量）
        '问题', '方法', '系统', '方式', '过程', '情况', '方面', '内容',
        '部分', '东西', '事情', '地方', '样子', '道理', '感觉', '状态',
        '出来', '起来', '下来', '上去', '过来', '过去', '回来',
        '第一', '第二', '第三', '第四', '第五', '第六', '第七', '第八',
        '没有', '不是', '不会', '不能', '不要', '不用', '没有',
    ])

    def __init__(self, notes_dir: str):
        """
        Args:
            notes_dir: 笔记目录路径
        """
        self.notes_dir = Path(notes_dir)
        self._index: Dict[str, NoteSummary] = {}
        self._idf: Dict[str, float] = {}
        self._content_cache: Dict[str, str] = {}  # 内容缓存，避免重复读文件
        self._built = False

    def build_index(self) -> int:
        """
        构建笔记索引

        Returns:
            索引的笔记数量
        """
        self._index.clear()
        self._content_cache.clear()
        doc_freq: Counter = Counter()  # 每个词出现在多少文档中
        total_docs = 0

        for md_file in sorted(self.notes_dir.glob('*.md')):
            # 跳过合成产物和知识体系索引文件
            skip_prefixes = ('knowledge_synthesis', 'mental_models', 'action_playbook')
            skip_keywords = ('知识体系', 'knowledge_framework')
            if (md_file.stem.startswith(skip_prefixes)
                    or any(kw in md_file.stem for kw in skip_keywords)):
                continue
            try:
                content = md_file.read_text(encoding='utf-8')
                resolved = str(md_file.resolve())
                # 缓存内容，避免搜索时重复读文件
                self._content_cache[resolved] = content

                summary = self._extract_summary(md_file, content)
                # 使用 resolve() 确保路径一致性
                resolved = str(md_file.resolve())
                summary.path = resolved
                self._index[resolved] = summary

                # 统计词频用于 TF-IDF
                words = set(self._tokenize(content))
                for word in words:
                    doc_freq[word] += 1
                total_docs += 1

            except Exception:
                continue

        # 计算 IDF
        if total_docs > 0:
            self._idf = {
                word: math.log(total_docs / (1 + freq))
                for word, freq in doc_freq.items()
            }

        self._built = True
        return len(self._index)

    def search(self, query: str, limit: int = 10,
               tags: List[str] = None) -> List[SearchResult]:
        """
        搜索笔记

        Args:
            query: 搜索关键词（支持空格分隔多关键词，也支持中文连续词）
            limit: 返回结果数上限
            tags: 按标签过滤

        Returns:
            搜索结果列表（按相关度排序）
        """
        if not self._built:
            self.build_index()

        # 使用 jieba 分词而非简单空格分割（支持中文连续查询词）
        keywords = self._tokenize(query)
        # 同时保留原始查询词作为整体匹配（应对 jieba 切分不准的情况）
        raw_query = query.strip()
        if raw_query and raw_query not in keywords and len(raw_query) >= 2:
            keywords.append(raw_query)

        if not keywords:
            return []

        results: List[SearchResult] = []

        for path, summary in self._index.items():
            # 标签过滤
            if tags:
                if not any(t in summary.tags for t in tags):
                    continue

            # 计算相关度
            relevance = self._compute_relevance(path, keywords)
            if relevance <= 0:
                continue

            # 提取匹配片段
            snippet = self._extract_snippet(path, keywords)

            results.append(SearchResult(
                path=path,
                title=summary.title,
                relevance=relevance,
                snippet=snippet,
                tags=summary.tags,
                date=summary.date,
            ))

        # 按相关度排序
        results.sort(key=lambda r: r.relevance, reverse=True)
        return results[:limit]

    def get_note_summary(self, note_path: str) -> Optional[NoteSummary]:
        """获取单篇笔记摘要"""
        if not self._built:
            self.build_index()
        return self._index.get(str(Path(note_path).resolve()))

    def get_all_tags(self) -> Dict[str, int]:
        """
        获取所有标签及其出现次数

        Returns:
            {tag: count}
        """
        if not self._built:
            self.build_index()

        tag_counts: Counter = Counter()
        for summary in self._index.values():
            for tag in summary.tags:
                tag_counts[tag] += 1
        return dict(tag_counts.most_common())

    def find_related_notes(self, content: str, limit: int = 5) -> List[Tuple[str, float]]:
        """
        找到与给定内容最相关的已有笔记

        Args:
            content: 文本内容
            limit: 返回数量

        Returns:
            [(note_path, relevance_score), ...]
        """
        if not self._built:
            self.build_index()

        keywords = self._extract_keywords(content, top_n=20)
        if not keywords:
            return []

        results: List[Tuple[str, float]] = []
        for path in self._index:
            score = self._compute_relevance(path, keywords)
            if score > 0:
                results.append((path, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def list_notes(self) -> List[NoteSummary]:
        """列出所有笔记摘要"""
        if not self._built:
            self.build_index()
        return sorted(
            self._index.values(),
            key=lambda s: s.date, reverse=True
        )

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _extract_summary(self, md_file: Path, content: str) -> NoteSummary:
        """从笔记文件提取摘要"""
        # 提取标题
        title = ""
        for line in content.split('\n'):
            if line.startswith('# '):
                title = line[2:].strip()
                break
        if not title:
            title = md_file.stem

        # 提取日期
        date = ""
        date_match = re.search(r'笔记整理时间[：:]\s*(\d{4}-\d{2}-\d{2})', content)
        if date_match:
            date = date_match.group(1)
        else:
            # 从文件修改时间获取
            mtime = os.path.getmtime(md_file)
            date = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')

        # 提取标签（自动）
        tags = self._extract_auto_tags(content)

        # 提取框架
        frameworks: List[str] = []
        for match in re.finditer(r'###?\s+(?:框架|模型|方法|步骤|流程)[：:]*\s*(.+)', content):
            frameworks.append(match.group(1).strip()[:50])

        # 提取行动项
        actions: List[str] = []
        for match in re.finditer(r'- \[ \]\s*(.+)', content):
            actions.append(match.group(1).strip()[:80])

        # 统计
        clean_text = re.sub(r'[#*|\-\[\]()>]', '', content)
        char_count = len(clean_text.replace('\n', '').replace(' ', ''))
        words = self._tokenize(content)
        word_count = len(words)

        return NoteSummary(
            path=str(md_file),
            title=title,
            date=date,
            tags=tags,
            key_frameworks=frameworks,
            action_items=actions,
            char_count=char_count,
            word_count=word_count,
        )

    def _extract_auto_tags(self, content: str, top_n: int = 8) -> List[str]:
        """自动提取标签（jieba 分词 + TF-IDF）"""
        words = self._tokenize(content)
        if not words:
            return []

        # TF 统计
        tf = Counter(words)
        total = len(words)

        # TF-IDF 评分
        scores = {}
        for word, count in tf.items():
            if len(word) < 2:
                continue
            tf_score = count / total
            idf_score = self._idf.get(word, math.log(10))  # 新词用默认 IDF
            scores[word] = tf_score * idf_score

        # 取 top_n
        top_words = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [word for word, _ in top_words[:top_n]]

    def _tokenize(self, text: str) -> List[str]:
        """中文分词"""
        import jieba
        # 清理 Markdown 语法
        clean = re.sub(r'[#*|\-\[\]()>`~]', '', text)
        clean = re.sub(r'https?://\S+', '', clean)
        words = jieba.lcut(clean)
        # 过滤停用词和短词
        return [w for w in words
                if w not in self.STOP_WORDS
                and len(w) >= 2
                and not w.isdigit()
                and not re.match(r'^[\W\s]+$', w)]

    def _compute_relevance(self, note_path: str, keywords: List[str]) -> float:
        """计算笔记与关键词的相关度（使用缓存避免重复读文件）"""
        summary = self._index.get(note_path)
        if not summary:
            return 0.0

        # 使用缓存内容
        content = self._content_cache.get(note_path)
        if not content:
            try:
                content = Path(note_path).read_text(encoding='utf-8')
                self._content_cache[note_path] = content
            except Exception:
                return 0.0

        content_lower = content.lower()
        title_lower = summary.title.lower()

        score = 0.0
        for kw in keywords:
            kw_lower = kw.lower()

            # 标题匹配（权重最高）
            if kw_lower in title_lower:
                score += 3.0

            # 标签匹配
            if any(kw_lower in tag for tag in summary.tags):
                score += 2.0

            # 全文匹配（按出现次数）
            count = content_lower.count(kw_lower)
            if count > 0:
                score += min(count * 0.5, 3.0)  # 上限 3.0

            # 框架匹配
            if any(kw_lower in fw.lower() for fw in summary.key_frameworks):
                score += 1.5

        # 归一化到 0-1
        max_possible = len(keywords) * 9.5  # 每个关键词最高分
        return min(1.0, score / max_possible) if max_possible > 0 else 0.0

    def _extract_keywords(self, text: str, top_n: int = 10) -> List[str]:
        """从文本中提取关键词"""
        words = self._tokenize(text)
        counter = Counter(words)
        return [w for w, _ in counter.most_common(top_n)]

    def _extract_snippet(self, note_path: str, keywords: List[str],
                          context_chars: int = 100) -> str:
        """提取匹配片段（使用缓存）"""
        content = self._content_cache.get(note_path)
        if not content:
            try:
                content = Path(note_path).read_text(encoding='utf-8')
            except Exception:
                return ""

        content_lower = content.lower()

        # 找到第一个关键词出现的位置
        best_pos = -1
        for kw in keywords:
            pos = content_lower.find(kw.lower())
            if pos >= 0 and (best_pos < 0 or pos < best_pos):
                best_pos = pos

        if best_pos < 0:
            return content[:context_chars * 2] + "..."

        # 提取上下文
        start = max(0, best_pos - context_chars)
        end = min(len(content), best_pos + context_chars)
        snippet = content[start:end].replace('\n', ' ').strip()

        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."

        return snippet
