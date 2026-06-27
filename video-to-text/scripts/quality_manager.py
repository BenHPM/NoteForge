# -*- coding: utf-8 -*-
"""
NoteForge 质量管理模块
提取自 llm_note_engine.py 的质量门禁、报告保存/打印、中间结果保存逻辑
"""

import os
import json
import tempfile
from pathlib import Path
from typing import Optional

# 延迟导入 quality_gate（避免循环依赖）
_quality_gate = None


def _get_quality_gate(config: dict = None):
    """延迟获取 QualityGate 单例"""
    global _quality_gate
    if _quality_gate is None:
        try:
            from quality_gate import QualityGate
            quality_cfg = (config or {}).get('quality', {})
            _quality_gate = QualityGate(
                fatal_rules_must_pass=quality_cfg.get('fatal_rules_must_pass', True),
                rules_path=str(Path(__file__).parent.parent / 'config' / 'note_generation_rules.yaml'),
            )
        except ImportError:
            import logging
            logging.getLogger('noteforge').warning(
                "无法导入 quality_gate 模块，将跳过质量检查"
            )
    return _quality_gate


class QualityManager:
    """质量门禁与报告管理器"""

    def __init__(self, reports_dir, notes_dir, base_dir, logger, config=None):
        """
        Args:
            reports_dir: 质量报告输出目录 (Path)
            notes_dir: 笔记输出目录 (Path)
            base_dir: 项目根目录 (Path)
            logger: 日志记录器
            config: 引擎配置字典（可选）
        """
        self._reports_dir = reports_dir
        self._notes_dir = notes_dir
        self._base_dir = base_dir
        self.logger = logger
        self._config = config

    def check_only(self, note_path: str, transcript_path: str) -> Optional[dict]:
        """
        仅运行质量检查

        Args:
            note_path: 笔记文件路径
            transcript_path: 对应的转写文件路径

        Returns:
            质量报告字典
        """
        report = self.run_quality_gate(note_path, transcript_path)
        if report:
            self.save_quality_report(note_path, report)
            self.print_quality_report(report)
        return report

    def run_quality_gate(self, note_path: str,
                         transcript_path: str) -> Optional[dict]:
        """运行质量门禁（文件路径版本）"""
        gate = _get_quality_gate(self._config)
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
        """运行质量门禁（文本版本，写临时文件）"""
        gate = _get_quality_gate(self._config)
        if gate is None:
            return None

        note_tmp = None
        transcript_tmp = None
        try:
            # 写临时文件
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.md', delete=False,
                encoding='utf-8'
            ) as f:
                f.write(note_text)
                note_tmp = f.name

            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.txt', delete=False,
                encoding='utf-8'
            ) as f:
                f.write(transcript)
                transcript_tmp = f.name

            report = gate.evaluate(note_tmp, transcript_tmp)
            return report.to_dict()
        except Exception as e:
            self.logger.warning(f"质量检查失败: {e}")
            return None
        finally:
            # 清理临时文件
            for tmp in (note_tmp, transcript_tmp):
                if tmp is not None:
                    try:
                        os.unlink(tmp)
                    except Exception:
                        pass

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
