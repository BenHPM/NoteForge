"""
topic_classifier.py — 笔记主题分类器（TF-IDF + LLM 双层策略）

功能:
  1. TF-IDF 快速匹配: 新笔记与已有主题做相似度，高置信度直接归类
  2. LLM 语义分类: TF-IDF 不确定时调用 LLM 判断主题
  3. 聚类扫描: 定期检测「其他笔记」中是否有新主题涌现

用法:
    python topic_classifier.py classify "第01集-xxx.md"   # 分类单篇笔记
    python topic_classifier.py scan                        # 扫描聚类
    python topic_classifier.py report                      # 输出主题报告
"""

import os
import sys
import json
import math
import re
import logging
import argparse
from pathlib import Path
from collections import Counter, defaultdict
from typing import Optional

# 修复 Windows 控制台编码
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import env_check  # noqa: F401 — 检测 Python 环境

from knowledge_index import KnowledgeIndex
from llm_providers import create_provider, LLMProvider, LLMError

logger = logging.getLogger('noteforge.topic')

# ============================================================
# 配置
# ============================================================

# TF-IDF 相似度阈值: 高于此值直接归类，不调用 LLM
TFIDF_AUTO_CLASSIFY_THRESHOLD = 0.35

# 聚类最小簇大小: 同主题笔记数达到此值才建议创建新分支
CLUSTER_MIN_SIZE = 3

# LLM 分类的置信度阈值: 低于此值标记为「待人工确认」
LLM_CONFIDENCE_THRESHOLD = 0.6


# ============================================================
# 主题定义
# ============================================================

# 已有主题 → 关键词（从 config/categories 提取 + 手动维护）
# 新增主题时只需在此添加条目
TOPIC_PROFILES: dict[str, dict] = {
    "短视频导演课程": {
        "keywords": [
            "短视频", "导演", "拍摄", "剪辑", "文案", "封面", "标题",
            "IP", "网红", "流量", "爆款", "脚本", "分镜", "运镜",
            "BGM", "转场", "完播率", "涨粉", "变现", "广告",
        ],
        "description": "短视频创作全流程：拍摄、剪辑、文案、运营、变现",
        "feishu_path": "AI笔记库/短视频导演课程",
    },
}


def get_known_topics() -> list[str]:
    """获取所有已知主题名称。"""
    return list(TOPIC_PROFILES.keys())


# ============================================================
# Layer 1: TF-IDF 快速匹配
# ============================================================

class TFIDFMatcher:
    """基于 TF-IDF 的笔记主题快速匹配。"""

    def __init__(self, notes_dir: Path):
        self.index = KnowledgeIndex(str(notes_dir))
        self.index.build_index()
        self._topic_vectors: dict[str, dict[str, float]] = {}
        self._build_topic_vectors()

    def _build_topic_vectors(self) -> None:
        """为每个已有主题构建 TF-IDF 向量（基于主题关键词在笔记中的出现）。"""
        for topic, profile in TOPIC_PROFILES.items():
            keywords = profile.get("keywords", [])
            # 用关键词在笔记索引中搜索，统计每个关键词的 IDF 权重
            vector: dict[str, float] = {}
            for kw in keywords:
                results = self.index.search(kw, limit=50)
                if results:
                    # 关键词在越多笔记中出现，权重越低（IDF 思想）
                    idf = math.log(len(self.index._index) / (1 + len(results)))
                    vector[kw] = max(0.5, idf)
            self._topic_vectors[topic] = vector

    def classify(self, note_path: str) -> list[tuple[str, float]]:
        """
        用 TF-IDF 匹配笔记最可能的主题。

        Returns:
            [(topic, confidence), ...] 按置信度降序
        """
        try:
            content = Path(note_path).read_text(encoding='utf-8')
        except Exception as e:
            logger.debug(f"分类失败: {e}")
            return []

        content_lower = content.lower()
        scores: dict[str, float] = {}

        for topic, vector in self._topic_vectors.items():
            score = 0.0
            matched_keywords = 0
            for kw, weight in vector.items():
                count = content_lower.count(kw.lower())
                if count > 0:
                    score += min(count * 0.3, 2.0) * weight
                    matched_keywords += 1

            # 归一化: 除以关键词总数
            if vector:
                coverage = matched_keywords / len(vector)
                normalized = score / sum(vector.values())
                scores[topic] = normalized * (0.5 + 0.5 * coverage)

        # 排序返回
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [(t, round(s, 3)) for t, s in sorted_scores if s > 0.05]

    def find_related_notes(self, note_path: str, limit: int = 5) -> list[tuple[str, float]]:
        """找到与给定笔记最相关的已有笔记。"""
        try:
            content = Path(note_path).read_text(encoding='utf-8')
        except Exception as e:
            logger.debug(f"分类失败: {e}")
            return []
        return self.index.find_related_notes(content, limit=limit)


# ============================================================
# Layer 2: LLM 语义分类
# ============================================================

LLM_CLASSIFY_PROMPT = """你是一个笔记分类专家。请判断以下笔记属于哪个已有主题，或者是否是一个新主题。

## 已有主题
{topics_info}

## 笔记内容（前 2000 字）
{note_excerpt}

## 输出要求
返回 JSON 格式:
{{
  "topic": "主题名称（已有主题名 或 新主题建议名）",
  "is_new_topic": true/false,
  "confidence": 0.0~1.0,
  "reason": "一句话说明判断依据"
}}

规则:
- 如果笔记内容与某个已有主题高度匹配，选该主题
- 如果内容与所有已有主题都不匹配，建议一个新主题名（2-4 个字）
- confidence 表示你对这个判断的确信程度
- 只返回 JSON，不要其他文字"""


def _load_provider() -> LLMProvider:
    """从配置文件创建 LLM provider。"""
    config_path = BASE_DIR / "config" / "llm_engine_config.yaml"
    import yaml
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return create_provider(config.get('provider', {}))


class LLMClassifier:
    """基于 LLM 的笔记主题语义分类。"""

    def __init__(self, provider: Optional[LLMProvider] = None):
        if provider:
            self.provider = provider
        else:
            self.provider = _load_provider()

    def classify(self, note_path: str) -> dict:
        """
        用 LLM 判断笔记主题。

        Returns:
            {"topic": str, "is_new_topic": bool, "confidence": float, "reason": str}
        """
        try:
            content = Path(note_path).read_text(encoding='utf-8')
        except Exception as e:
            return {"topic": "", "is_new_topic": False, "confidence": 0.0, "reason": f"读取失败: {e}"}

        # 只取前 2000 字
        excerpt = content[:2000]

        # 构建已有主题信息
        topics_lines = []
        for topic, profile in TOPIC_PROFILES.items():
            desc = profile.get("description", "")
            kws = ", ".join(profile.get("keywords", [])[:10])
            topics_lines.append(f"- **{topic}**: {desc}（关键词: {kws}）")
        topics_info = "\n".join(topics_lines) if topics_lines else "（暂无已有主题）"

        prompt = LLM_CLASSIFY_PROMPT.format(
            topics_info=topics_info,
            note_excerpt=excerpt,
        )

        system_prompt = "你是一个笔记分类专家。请根据笔记内容判断它属于哪个主题，返回 JSON 格式。"

        try:
            response = self.provider.generate(system_prompt, prompt, temperature=0.2)
            # 提取 JSON
            json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return {"topic": "", "is_new_topic": False, "confidence": 0.0, "reason": "LLM 返回格式异常"}
        except (LLMError, json.JSONDecodeError) as e:
            return {"topic": "", "is_new_topic": False, "confidence": 0.0, "reason": str(e)}


# ============================================================
# Layer 3: 聚类扫描
# ============================================================

def scan_other_notes(notes_dir: Path) -> dict[str, list[str]]:
    """
    扫描「其他笔记」中未分类的笔记，用 TF-IDF 聚类。

    Returns:
        {潜在主题名: [笔记文件名列表]}
    """
    # 收集「其他笔记」目录下的文件
    other_notes: list[Path] = []
    for md_file in sorted(notes_dir.glob('*.md')):
        stem = md_file.stem
        # 排除已知课程笔记
        if re.match(r'^第\d+集', stem):
            continue
        if '知识体系' in stem:
            continue
        other_notes.append(md_file)

    if len(other_notes) < 2:
        return {}

    # 两两计算相似度
    index = KnowledgeIndex(str(notes_dir))
    index.build_index()

    # 提取每篇笔记的关键词
    note_keywords: dict[str, list[str]] = {}
    for note in other_notes:
        try:
            content = note.read_text(encoding='utf-8')
            words = index._tokenize(content)
            counter = Counter(words)
            note_keywords[note.stem] = [w for w, _ in counter.most_common(15)]
        except Exception as e:
            logger.debug(f"聚类跳过: {e}")
            continue

    # 简单聚类: 基于关键词重叠度
    clusters: list[list[str]] = []
    assigned: set[str] = set()

    stems = list(note_keywords.keys())
    for i, stem_a in enumerate(stems):
        if stem_a in assigned:
            continue
        cluster = [stem_a]
        kws_a = set(note_keywords[stem_a])

        for stem_b in stems[i + 1:]:
            if stem_b in assigned:
                continue
            kws_b = set(note_keywords[stem_b])
            overlap = len(kws_a & kws_b)
            union = len(kws_a | kws_b)
            jaccard = overlap / union if union > 0 else 0

            if jaccard > 0.15:  # Jaccard 相似度阈值
                cluster.append(stem_b)

        if len(cluster) >= CLUSTER_MIN_SIZE:
            clusters.append(cluster)
            assigned.update(cluster)

    # 转换为结果格式
    result: dict[str, list[str]] = {}
    for i, cluster in enumerate(clusters):
        # 用簇内关键词生成主题名
        all_kws: Counter = Counter()
        for stem in cluster:
            all_kws.update(note_keywords.get(stem, []))
        top_kws = [w for w, _ in all_kws.most_common(3)]
        topic_name = "/".join(top_kws) if top_kws else f"未命名主题{i + 1}"
        result[topic_name] = cluster

    return result


# ============================================================
# 组合分类器
# ============================================================

class TopicClassifier:
    """双层主题分类器: TF-IDF 快速匹配 + LLM 精确判断。"""

    def __init__(self, notes_dir: Optional[Path] = None):
        self.notes_dir = notes_dir or (BASE_DIR / "output" / "notes")
        self._tfidf: Optional[TFIDFMatcher] = None
        self._llm: Optional[LLMClassifier] = None

    def _get_tfidf(self) -> TFIDFMatcher:
        if self._tfidf is None:
            self._tfidf = TFIDFMatcher(self.notes_dir)
        return self._tfidf

    def _get_llm(self) -> LLMClassifier:
        if self._llm is None:
            self._llm = LLMClassifier()
        return self._llm

    def classify(self, note_path: str) -> dict:
        """
        对单篇笔记执行双层分类。

        Returns:
            {
                "note": str,
                "topic": str,
                "method": "tfidf" | "llm",
                "confidence": float,
                "is_new_topic": bool,
                "reason": str,
                "tfidf_scores": [(topic, score), ...],
            }
        """
        note_name = Path(note_path).stem
        logger.info(f"分类笔记: {note_name}")

        # Layer 1: TF-IDF 快速匹配
        tfidf = self._get_tfidf()
        tfidf_scores = tfidf.classify(note_path)

        if tfidf_scores:
            best_topic, best_score = tfidf_scores[0]
            logger.info(f"  TF-IDF: {best_topic} ({best_score:.3f})")

            if best_score >= TFIDF_AUTO_CLASSIFY_THRESHOLD:
                logger.info(f"  → 高置信度，直接归类为「{best_topic}」")
                return {
                    "note": note_name,
                    "topic": best_topic,
                    "method": "tfidf",
                    "confidence": best_score,
                    "is_new_topic": False,
                    "reason": f"TF-IDF 匹配 {best_topic}，相似度 {best_score:.3f}",
                    "tfidf_scores": tfidf_scores,
                }

        # Layer 2: LLM 语义分类
        logger.info("  TF-IDF 置信度不足，调用 LLM...")
        llm = self._get_llm()
        llm_result = llm.classify(note_path)

        logger.info(f"  LLM: {llm_result.get('topic', '?')} "
                     f"(confidence={llm_result.get('confidence', 0):.2f}, "
                     f"new={llm_result.get('is_new_topic', False)})")

        return {
            "note": note_name,
            "topic": llm_result.get("topic", ""),
            "method": "llm",
            "confidence": llm_result.get("confidence", 0.0),
            "is_new_topic": llm_result.get("is_new_topic", False),
            "reason": llm_result.get("reason", ""),
            "tfidf_scores": tfidf_scores,
        }

    def scan_new_topics(self) -> dict:
        """
        扫描「其他笔记」，检测是否有新主题涌现。

        Returns:
            {
                "clusters": {主题: [笔记列表]},
                "recommendations": [建议创建的新分支],
            }
        """
        logger.info("扫描「其他笔记」聚类...")
        clusters = scan_other_notes(self.notes_dir)

        recommendations = []
        for topic, notes in clusters.items():
            if len(notes) >= CLUSTER_MIN_SIZE:
                recommendations.append({
                    "suggested_name": topic,
                    "note_count": len(notes),
                    "notes": notes,
                    "action": f"建议创建新分支「{topic}」，包含 {len(notes)} 篇笔记",
                })

        return {
            "clusters": clusters,
            "recommendations": recommendations,
        }

    def report(self) -> str:
        """输出当前主题分布报告。"""
        lines = ["=" * 50, "NoteForge 主题分布报告", "=" * 50, ""]

        # 统计每个主题的笔记数
        if not self.notes_dir.exists():
            return "笔记目录不存在"

        notes = list(self.notes_dir.glob('*.md'))
        topic_counts: dict[str, int] = defaultdict(int)
        uncategorized: list[str] = []

        for note in notes:
            stem = note.stem
            if re.match(r'^第\d+集', stem) or '知识体系' in stem:
                topic_counts["短视频导演课程"] += 1
            else:
                uncategorized.append(stem)

        topic_counts["其他笔记（未分类）"] = len(uncategorized)

        for topic, count in sorted(topic_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {topic}: {count} 篇")

        if uncategorized:
            lines.append(f"\n  未分类笔记:")
            for name in uncategorized:
                lines.append(f"    - {name}")

        return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='NoteForge 主题分类器')
    sub = parser.add_subparsers(dest='command')

    # classify 子命令
    p_classify = sub.add_parser('classify', help='分类单篇笔记')
    p_classify.add_argument('note', help='笔记文件路径')

    # scan 子命令
    sub.add_parser('scan', help='扫描聚类，检测新主题')

    # report 子命令
    sub.add_parser('report', help='输出主题分布报告')

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    classifier = TopicClassifier()

    if args.command == 'classify':
        result = classifier.classify(args.note)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.command == 'scan':
        result = classifier.scan_new_topics()
        if result['recommendations']:
            print("\n发现新主题聚类:")
            for rec in result['recommendations']:
                print(f"  - {rec['action']}")
                for note in rec['notes']:
                    print(f"    · {note}")
        else:
            print("\n未发现新的主题聚类。")
        print(f"\n聚类详情: {json.dumps(result['clusters'], ensure_ascii=False, indent=2)}")

    elif args.command == 'report':
        print(classifier.report())

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
