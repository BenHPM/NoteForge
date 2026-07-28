# -*- coding: utf-8 -*-
"""
NoteForge 质量趋势追踪

每次质量评估后追加记录到 flat JSON 文件，
支持跨会话的历史趋势分析。

存储格式: output/logs/quality_trend.json
"""

import json
import os
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger('noteforge.quality_trend')


class QualityTrend:
    """质量趋势追踪器 — 追加式 flat JSON"""

    def __init__(self, log_dir: str = None):
        if log_dir is None:
            log_dir = str(Path(__file__).resolve().parent.parent.parent / "output" / "logs")
        self._path = Path(log_dir) / "quality_trend.json"
        self._records: List[Dict[str, Any]] = []
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        if self._path.exists():
            try:
                self._records = json.loads(self._path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError):
                self._records = []
        self._loaded = True

    def record(self, note_path: str, report: Dict[str, Any],
               domain: str = "", content_type: str = "",
               duration_seconds: float = 0, attempts: int = 1) -> None:
        """记录一次质量评估结果

        Args:
            note_path: 笔记文件路径
            report: QualityReport.to_dict() 的输出
            domain: 知识域 ID
            content_type: 内容类型
            duration_seconds: 处理耗时
            attempts: 尝试次数
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "note_stem": Path(note_path).stem,
            "domain": domain,
            "content_type": content_type,
            "total_score": report.get("total_score", 0),
            "overall_passed": report.get("overall_passed", False),
            "rule_scores": {
                rid: rr.get("score", 0)
                for rid, rr in report.get("rule_results", {}).items()
            },
            "duration_seconds": round(duration_seconds, 1),
            "attempts": attempts,
        }

        # LLM 评审（可选）
        llm_eval = report.get("llm_eval")
        if llm_eval:
            entry["llm_eval"] = llm_eval

        # 备注：note_path 太长，只存 stem + domain 就够了
        # 如需回溯，通过 stem 在 notes/ 目录查找

        self._records.append(entry)
        self._persist()
        logger.debug(f"Trend recorded: {entry['note_stem']} score={entry['total_score']:.0%}")

    def _persist(self):
        """原子写入"""
        tmp = self._path.with_suffix('.json.tmp')
        try:
            tmp.write_text(
                json.dumps(self._records, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            tmp.replace(self._path)
        except OSError:
            pass

    def get_records(self, domain: str = "", since: str = "",
                    min_score: float = -1) -> List[Dict[str, Any]]:
        """查询历史记录

        Args:
            domain: 按知识域过滤（空字符串 = 全部）
            since: ISO timestamp 起始时间（空字符串 = 全部）
            min_score: 最低总分过滤（-1 = 不过滤）

        Returns:
            符合条件的记录列表
        """
        self._ensure_loaded()
        results = self._records
        if domain:
            results = [r for r in results if r.get("domain") == domain]
        if since:
            results = [r for r in results if r.get("timestamp", "") >= since]
        if min_score >= 0:
            results = [r for r in results if r.get("total_score", 0) >= min_score]
        return results

    def get_stats(self, domain: str = "") -> Dict[str, Any]:
        """统计趋势摘要

        Returns:
            {
                "total": int,
                "avg_score": float,
                "pass_rate": float,
                "domain_scores": {domain: avg_score},
                "recent_5_avg": float,
            }
        """
        records = self.get_records(domain=domain)
        if not records:
            return {"total": 0}

        scores = [r["total_score"] for r in records]
        passed = [r for r in records if r.get("overall_passed")]
        recent = records[-5:] if len(records) >= 5 else records

        domain_scores: Dict[str, List[float]] = {}
        for r in records:
            d = r.get("domain", "unknown")
            domain_scores.setdefault(d, []).append(r["total_score"])

        return {
            "total": len(records),
            "avg_score": sum(scores) / len(scores),
            "pass_rate": len(passed) / len(records),
            "domain_scores": {
                d: sum(ss) / len(ss)
                for d, ss in domain_scores.items()
            },
            "recent_5_avg": sum(r["total_score"] for r in recent) / len(recent),
            "min_score": min(scores),
            "max_score": max(scores),
        }

    def print_trend(self, domain: str = "") -> None:
        """打印趋势概览"""
        stats = self.get_stats(domain=domain)
        if stats.get("total", 0) == 0:
            print("  No quality records yet.")
            return

        d_label = f" [{domain}]" if domain else ""
        print(f"\n  Quality Trend{d_label}:")
        print(f"  Total evaluations: {stats['total']}")
        print(f"  Average score:     {stats['avg_score']:.0%}")
        print(f"  Pass rate:         {stats['pass_rate']:.0%}")
        print(f"  Recent 5 avg:      {stats['recent_5_avg']:.0%}")
        print(f"  Range:             {stats['min_score']:.0%} ~ {stats['max_score']:.0%}")

        domain_scores = stats.get("domain_scores", {})
        if domain_scores:
            print(f"\n  By domain:")
            for d, s in sorted(domain_scores.items(), key=lambda x: -x[1]):
                bar = "#" * int(s * 20)
                print(f"    {d:25s} {s:.0%} {bar}")

    @property
    def record_count(self) -> int:
        self._ensure_loaded()
        return len(self._records)

    def clear(self) -> None:
        """清空所有记录（谨慎使用）"""
        self._records = []
        self._persist()
