# -*- coding: utf-8 -*-
"""
NoteForge 质量管理模块
提取自 llm_note_engine.py 的质量门禁、报告保存/打印、中间结果保存逻辑
"""

import os
import json
import threading
from pathlib import Path
from typing import Optional

__all__ = ['QualityManager', 'reset_quality_gate', '_get_quality_gate']

# 延迟导入 quality_gate（避免循环依赖）
_quality_gate = None
_quality_gate_content_type = None
_gate_lock = threading.Lock()


def _get_quality_gate(config: dict = None, content_type: str = None,
                      llm_eval_provider=None, llm_eval_on_borderline: bool = False):
    """延迟获取 QualityGate 单例（content_type 变化时重建，线程安全）。"""
    global _quality_gate, _quality_gate_content_type
    # content_type 变化时需要重建，因为 R4 概念检查依赖领域
    # LLM 评审参数变化时也需要重建
    rebuild_needed = (
        _quality_gate is None
        or _quality_gate_content_type != content_type
        or (llm_eval_on_borderline and _quality_gate._llm_eval_provider is None)
    )
    if not rebuild_needed:
        return _quality_gate
    with _gate_lock:
        # 双检查：lock 内再次检查，避免其他线程已重建
        if _quality_gate is not None and _quality_gate_content_type == content_type:
            if not (llm_eval_on_borderline and _quality_gate._llm_eval_provider is None):
                return _quality_gate
        try:
            from noteforge.quality.gate import QualityGate
            quality_cfg = (config or {}).get('quality', {})
            _quality_gate = QualityGate(
                fatal_rules_must_pass=quality_cfg.get('fatal_rules_must_pass', True),
                rules_path=str(Path(__file__).parent.parent.parent / 'config' / 'note_generation_rules.yaml'),
                content_type=content_type,
                llm_eval_provider=llm_eval_provider,
                llm_eval_on_borderline=llm_eval_on_borderline,
                llm_eval_borderline_low=quality_cfg.get('llm_eval_borderline_low', 0.75),
                llm_eval_borderline_high=quality_cfg.get('llm_eval_borderline_high', 0.85),
            )
            _quality_gate_content_type = content_type
        except ImportError:
            import logging
            logging.getLogger('noteforge').warning(
                "无法导入 quality_gate 模块，将跳过质量检查"
            )
            return None
    return _quality_gate


def reset_quality_gate():
    """重置 QualityGate 单例（content_type 切换后调用，避免状态不一致）。"""
    global _quality_gate, _quality_gate_content_type
    with _gate_lock:
        _quality_gate = None
        _quality_gate_content_type = None


class QualityManager:
    """质量门禁与报告管理器"""

    def __init__(self, path_config, logger, config=None, content_type=None):
        """
        Args:
            path_config: PathConfig 共享路径配置
            logger: 日志记录器
            config: 引擎配置字典（可选）
            content_type: 内容类型，影响 R4 概念检查领域（可选）
        """
        self._path_config = path_config
        self.logger = logger
        self._config = config
        self._content_type = content_type
        self._trend: Optional['QualityTrend'] = None

    def set_trend(self, trend: 'QualityTrend') -> None:
        """设置趋势追踪器（可选，不设置则跳过记录）"""
        self._trend = trend

    # 兼容属性（委托到 _path_config）
    @property
    def _reports_dir(self):
        return self._path_config.reports_dir

    @property
    def _notes_dir(self):
        return self._path_config.notes_dir

    @property
    def _base_dir(self):
        return self._path_config.base_dir

    def check_only(self, note_path: str, transcript_path: str) -> Optional[dict]:
        report = self.run_quality_gate(note_path, transcript_path)
        if report:
            self.save_quality_report(note_path, report)
            self._record_trend(note_path, report)
            self.print_quality_report(report)
        return report

    def _record_trend(self, note_path: str, report: dict) -> None:
        """记录趋势（如果已设置）"""
        if self._trend is None:
            return
        try:
            # 推断知识域（从文件名）
            domain = ""
            try:
                from noteforge.core.domain_classifier import DomainClassifier
                domains = (self._config or {}).get('knowledge_domains', [])
                clf = DomainClassifier(domains=domains, path_config=self._path_config)
                domain = clf.detect_domain(Path(note_path).stem)
            except Exception:
                pass
            self._trend.record(
                note_path=note_path,
                report=report,
                domain=domain,
                content_type=self._content_type or "",
            )
        except Exception as e:
            self.logger.debug(f"趋势记录跳过: {e}")

    def run_quality_gate(self, note_path: str,
                         transcript_path: str) -> Optional[dict]:
        """运行质量门禁（文件路径版本）"""
        gate = _get_quality_gate(self._config, content_type=self._content_type)
        if gate is None:
            return None
        try:
            report = gate.evaluate(note_path, transcript_path)
            return report.to_dict()
        except Exception as e:
            self.logger.warning(f"质量检查失败: {e}")
            return None

    def run_quality_gate_on_text(self, note_text: str,
                                 transcript: str) -> Optional[dict]:
        """运行质量门禁（文本版本，零临时文件）"""
        gate = _get_quality_gate(self._config, content_type=self._content_type)
        if gate is None:
            return None
        try:
            report = gate.evaluate_text(note_text, transcript)
            return report.to_dict()
        except Exception as e:
            self.logger.warning(f"质量检查失败: {e}")
            return None

    def save_quality_report(self, note_path: str, report: dict):
        """保存质量报告"""
        stem = Path(note_path).stem
        report_path = self._reports_dir / f"{stem}_quality.json"
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        self.logger.debug(f"质量报告: {report_path}")

    def save_intermediate(self, title: str, attempt: int, text: str,
                          logs_dir: Path):
        """保存中间 LLM 输出"""
        safe_name = title.replace(' ', '_').replace('/', '_')[:30]
        path = logs_dir / f"{safe_name}_attempt{attempt}.md"
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        self.logger.debug(f"中间结果: {path}")

    def print_quality_report(self, report: dict):
        """打印质量报告"""
        print("\n" + "=" * 60)
        print("  \U0001f4ca 质量评估报告")
        print("=" * 60)
        total = report.get('total_score', 0)
        passed = report.get('overall_passed', False)
        print(f"  综合评分: {total:.0%} {'✅ 通过' if passed else '❌ 未通过'}")
        print()
        for rid in ['R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9', 'R10', 'R11', 'R12']:
            rr = report.get('rule_results', {}).get(rid, {})
            if rr:
                score = rr.get('score', 0)
                ok = '✅' if rr.get('passed', False) else '❌'
                issues = len(rr.get('issues', []))
                print(f"  {ok} {rid}: {score:.0%} ({issues} 个问题)")
        print("=" * 60)
