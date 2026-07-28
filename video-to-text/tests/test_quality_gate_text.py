# -*- coding: utf-8 -*-
"""
QualityGate Level 1 解耦测试

覆盖新增入口：
  - evaluate_text(): 纯文本入口，不依赖文件 IO
  - evaluate_rule(): 单条规则调试入口
  - evaluate_text() 返回的报告自包含文本内容
  - evaluate() 委托给 evaluate_text() 行为一致

运行:
    cd video-to-text
    envs/paraformer/python.exe -m pytest tests/test_quality_gate_text.py -v
"""
import os
import pytest

# ─── 测试数据 ───

LONG_NOTE = """# 短视频创作笔记

> 课程定位：短视频创作

---

## 核心要点

导演的核心能力是把视觉语言转化为观众能感知的情绪。短视频的节奏是每 3 秒一个信息点，
观众划走的成本极低，所以每个镜头的存在必须有明确目的。

爆火内容的三个共性：第一，开头 3 秒必须有冲突或悬念；第二，中间段提供信息增量的密度要够；
第三，结尾要有情绪出口或行动指引。

## 可迁移洞察

- 任何内容创作都可以套用"冲突→展开→收束"的三段结构
- 镜头语言的核心是引导视线，不是炫技
- 文案的 A/B 测试比直觉更可靠

## 实战经验

拍摄时先拍完所有远景，再拍特写。这样可以确保自然光的变化一致，
后期剪辑时匹配度更高。不要边拍边调参数。
"""

LONG_TRANSCRIPT = """主持人：今天我们来聊聊短视频创作的核心方法。首先，
导演的核心能力是把视觉语言转化为观众能感知的情绪。

嘉宾：对，短视频的节奏很关键。我观察到爆火内容有三个共性。第一，
开头3秒必须有冲突或悬念，不然观众就划走了。第二，
中间段的信息密度要够高，不能有废话。第三，
结尾必须有情绪出口或者行动指引。

主持人：那在镜头语言方面有什么建议？

嘉宾：镜头语言的核心是引导视线，不是炫技。用最简单的方式达到最直接的效果。
拍摄时先拍完所有远景，再拍特写，这样自然光的变化一致，后期剪辑匹配度更高。
"""

SHORT_NOTE = "太短了"


# ─── evaluate_text() ───

class TestEvaluateText:
    """evaluate_text() 纯文本入口测试"""

    @pytest.fixture
    def gate(self):
        from noteforge.quality.gate import QualityGate
        return QualityGate()

    def test_long_content_passes(self, gate):
        """足够长的笔记应通过 R0"""
        report = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT,
                                    note_label="ep01.md",
                                    source_label="ep01.txt")
        assert report.total_score > 0
        assert report.note_label == "ep01.md"
        assert report.source_label == "ep01.txt"

    def test_report_contains_note_text(self, gate):
        """evaluate_text 返回的报告应包含文本内容（自包含）"""
        report = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT)
        assert report.note_text == LONG_NOTE
        assert report.source_text == LONG_TRANSCRIPT

    def test_report_contains_labels(self, gate):
        """报告应存储可读标签"""
        report = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT,
                                    note_label="test.md", source_label="src.txt")
        assert report.note_label == "test.md"
        assert report.source_label == "src.txt"

    def test_default_labels(self, gate):
        """未指定标签时使用默认值"""
        report = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT)
        assert report.note_label == "<note>"
        assert report.source_label == "<source>"

    def test_short_content_fails_r0(self, gate):
        """R0: 短内容不通过（纯文本入口）"""
        report = gate.evaluate_text(SHORT_NOTE, LONG_TRANSCRIPT)
        assert not report.overall_passed
        assert "R0" in report.rule_results
        assert not report.rule_results["R0"].passed

    def test_report_includes_all_rules(self, gate):
        """报告应包含 R1-R12 全部规则"""
        report = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT)
        for rid in ["R1", "R2", "R3", "R4", "R5", "R6",
                     "R7", "R8", "R9", "R10", "R11", "R12"]:
            assert rid in report.rule_results, f"报告缺少 {rid}"

    def test_report_includes_metrics(self, gate):
        """报告应包含启发式质量指标"""
        report = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT)
        assert report.metrics is not None
        assert report.metrics.compression_ratio > 0

    def test_fabricated_data_detected(self, gate):
        """R1 应检测虚构数据（纯文本入口）"""
        note_with_fabricated = LONG_NOTE + "\n\n## 补充数据\n\n占比约50%，增长达到30%，第3名。"
        source_clean = LONG_TRANSCRIPT
        report = gate.evaluate_text(note_with_fabricated, source_clean)
        r1 = report.rule_results["R1"]
        assert not r1.passed or len(r1.issues) > 0

    def test_no_file_io_required(self, gate):
        """evaluate_text 不需要任何文件存在"""
        # 确保目标文件不存在
        report = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT)
        assert report is not None
        assert isinstance(report.total_score, float)


# ─── evaluate() 向后兼容 ───

class TestEvaluateBackwardCompat:
    """evaluate() 文件路径入口向后兼容测试"""

    @pytest.fixture
    def gate(self):
        from noteforge.quality.gate import QualityGate
        return QualityGate()

    def test_evaluate_returns_same_result_as_text(self, gate, tmp_path):
        """evaluate(paths) 结果应与 evaluate_text(texts) 一致"""
        note_file = tmp_path / "note.md"
        note_file.write_text(LONG_NOTE, encoding="utf-8")
        src_file = tmp_path / "source.txt"
        src_file.write_text(LONG_TRANSCRIPT, encoding="utf-8")

        report_from_paths = gate.evaluate(str(note_file), str(src_file))
        report_from_text = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT,
                                               note_label="note.md",
                                               source_label="source.txt")

        # 核心指标应一致
        assert report_from_paths.total_score == report_from_text.total_score
        assert report_from_paths.overall_passed == report_from_text.overall_passed
        assert report_from_paths.rule_results.keys() == report_from_text.rule_results.keys()

    def test_evaluate_sets_paths(self, gate, tmp_path):
        """evaluate() 应同时设置 note_path/source_path"""
        note_file = tmp_path / "note.md"
        note_file.write_text(LONG_NOTE, encoding="utf-8")
        src_file = tmp_path / "source.txt"
        src_file.write_text(LONG_TRANSCRIPT, encoding="utf-8")

        report = gate.evaluate(str(note_file), str(src_file))
        assert report.note_path == str(note_file)
        assert report.source_path == str(src_file)


# ─── evaluate_rule() 单规则调试 ───

class TestEvaluateRule:
    """evaluate_rule() 单规则调试入口测试"""

    def test_evaluate_rule_r1(self):
        """R1 单独运行应检测虚构数据"""
        from noteforge.quality.gate import QualityGate
        from noteforge.quality.models import RuleResult
        note = "# 标题\n占比约50%，增长达到30%"
        source = "市场增长显著"
        result = QualityGate.evaluate_rule("R1", note, source)
        assert isinstance(result, RuleResult)
        assert result.rule_id == "R1"
        assert result.rule_name == "禁止虚构数据"
        assert not result.passed or len(result.issues) > 0

    def test_evaluate_rule_r4(self):
        """R4 单独运行应检查概念失真"""
        from noteforge.quality.gate import QualityGate
        note = "T0策略就是简单的量化交易，通过高频交易实现"
        source = "T0策略是中低频长周期预测高抛低吸"
        result = QualityGate.evaluate_rule("R4", note, source,
                                            content_type="finance")
        assert result.rule_id == "R4"
        assert isinstance(result.passed, bool)

    def test_evaluate_rule_r8(self):
        """R8 单独运行应检测洞察可行动性"""
        from noteforge.quality.gate import QualityGate
        note = "# 可迁移洞察\n- 要重视投资\n- 需要关注市场变化"
        result = QualityGate.evaluate_rule("R8", note, "")
        assert result.rule_id == "R8"
        assert isinstance(result.passed, bool)

    def test_evaluate_rule_r5_coverage(self):
        """R5 单独运行应检查覆盖度"""
        from noteforge.quality.gate import QualityGate
        source = "# A\n内容A\n# B\n内容B\n# C\n内容C"
        note = "# 标题\n只有一点点"
        result = QualityGate.evaluate_rule("R5", note, source)
        assert result.rule_id == "R5"
        assert isinstance(result.passed, bool)

    def test_evaluate_rule_invalid_id(self):
        """无效规则 ID 应抛出 ValueError"""
        from noteforge.quality.gate import QualityGate
        with pytest.raises(ValueError, match="未知规则 ID"):
            QualityGate.evaluate_rule("R99", "note", "source")

    def test_evaluate_rule_returns_issues_with_details(self):
        """单规则结果应包含完整 Issue 信息"""
        from noteforge.quality.gate import QualityGate
        note = "占比约50%"
        source = "没有提到任何比例"
        result = QualityGate.evaluate_rule("R1", note, source)
        if result.issues:
            issue = result.issues[0]
            assert issue.rule_id == "R1"
            assert issue.severity in ("fatal", "major", "medium")
            assert len(issue.description) > 0
            assert len(issue.suggestion) > 0

    def test_evaluate_rule_all_rule_ids(self):
        """所有 R1-R12 规则 ID 都应能单独运行"""
        from noteforge.quality.gate import QualityGate
        for rid in [f"R{i}" for i in range(1, 13)]:
            result = QualityGate.evaluate_rule(rid, LONG_NOTE, LONG_TRANSCRIPT)
            assert result.rule_id == rid
            assert isinstance(result.passed, bool)

    def test_evaluate_rule_with_content_type(self):
        """evaluate_rule 应接受 content_type 参数"""
        from noteforge.quality.gate import QualityGate
        result = QualityGate.evaluate_rule("R4", LONG_NOTE, LONG_TRANSCRIPT,
                                           content_type="lecture")
        assert result.rule_id == "R4"


# ─── 边界条件 ───

class TestEdgeCases:
    """边界条件测试"""

    @pytest.fixture
    def gate(self):
        from noteforge.quality.gate import QualityGate
        return QualityGate()

    def test_empty_source(self, gate):
        """空原文不应崩溃"""
        report = gate.evaluate_text(LONG_NOTE, "")
        assert isinstance(report.total_score, float)

    def test_empty_note(self, gate):
        """空笔记应不通过"""
        report = gate.evaluate_text("", LONG_TRANSCRIPT)
        assert not report.overall_passed

    def test_unicode_content(self, gate):
        """Unicode 内容不应崩溃"""
        note = "# 翟东升谈地缘政治\n\n" + LONG_NOTE
        report = gate.evaluate_text(note, LONG_TRANSCRIPT)
        assert isinstance(report.total_score, float)

    def test_report_to_dict_serializable(self, gate):
        """报告应可序列化为 JSON"""
        import json
        report = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT,
                                    note_label="ep01.md")
        data = report.to_dict()
        json_str = json.dumps(data, ensure_ascii=False)
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["note_label"] == "ep01.md"

    def test_to_dict_no_paths_when_not_set(self, gate):
        """未设置路径时 to_dict 不应包含 path 字段"""
        report = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT)
        data = report.to_dict()
        assert "note_path" not in data
        assert "source_path" not in data

    def test_to_dict_includes_paths_when_set(self, gate, tmp_path):
        """设置了路径时 to_dict 应包含 path 字段"""
        note_file = tmp_path / "n.md"
        note_file.write_text(LONG_NOTE, encoding="utf-8")
        src_file = tmp_path / "s.txt"
        src_file.write_text(LONG_TRANSCRIPT, encoding="utf-8")
        report = gate.evaluate(str(note_file), str(src_file))
        data = report.to_dict()
        assert "note_path" in data
        assert "source_path" in data


# ─── 启发式指标护栏 ───

class TestMetricGuardrails:
    """启发式指标护栏测试（M1-M4）"""

    @pytest.fixture
    def gate(self):
        from noteforge.quality.gate import QualityGate
        return QualityGate()

    def test_low_info_density_auto_fails(self, gate):
        """M1: 信息密度极低时应自动失败
        注: info_density 基于 2-4 字中文词组多样性，空话笔记可能仍有较高值，
        此测试验证护栏逻辑在极端情况下能正确触发"""
        from unittest.mock import patch
        from noteforge.quality.heuristics import QualityMetrics

        # 构造一个 info_density 极低的 metrics 对象
        fake_metrics = QualityMetrics(
            compression_ratio=0.15,
            structure_score=0.5,
            info_density=0.10,  # 低于 0.15 阈值
            readability_score=0.3,
            quote_ratio=0.1,
            action_specificity=0.2,
            overall_richness=0.2,
        )
        with patch.object(gate, '_compute_metrics', return_value=fake_metrics):
            report = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT)
            m1_issues = [i for i in _all_issues(report) if i.rule_id == "M1"]
            assert len(m1_issues) > 0, "信息密度极低应触发 M1 护栏"
            assert m1_issues[0].severity == "fatal"
            assert not report.overall_passed  # M1 是 fatal，应导致不通过

    def test_high_quote_ratio_caps_score(self, gate):
        """M2: 引用比 > 0.5 应封顶分数并标记失败"""
        from unittest.mock import patch
        from noteforge.quality.heuristics import QualityMetrics

        # 构造一个 quote_ratio 过高的 metrics 对象
        fake_metrics = QualityMetrics(
            compression_ratio=0.20,
            structure_score=0.5,
            info_density=0.6,
            readability_score=0.5,
            quote_ratio=0.60,  # 高于 0.5 阈值
            action_specificity=0.5,
            overall_richness=0.5,
        )
        with patch.object(gate, '_compute_metrics', return_value=fake_metrics):
            report = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT)
            m2_issues = [i for i in _all_issues(report) if i.rule_id == "M2"]
            assert len(m2_issues) > 0, "引用比过高应触发 M2 护栏"
            assert report.total_score <= 0.70  # 分数应被封顶

    def test_normal_note_no_fatal_guardrails(self, gate):
        """正常笔记不应触发致命级护栏（M1, M2）
        注: M3(压缩比)和M4(结构)是 medium/major 级别，测试数据可能触发"""
        report = gate.evaluate_text(LONG_NOTE, LONG_TRANSCRIPT)
        fatal_guardrail_issues = [
            i for i in _all_issues(report)
            if i.rule_id.startswith("M") and i.severity == "fatal"
        ]
        assert len(fatal_guardrail_issues) == 0, \
            f"正常笔记不应触发致命护栏，但触发了: {[(i.rule_id, i.description) for i in fatal_guardrail_issues]}"

    def test_guardrail_issues_in_rule_results(self, gate):
        """护栏问题应出现在 rule_results 中"""
        low_density_note = """# 测试

> 课程定位：测试

---

## 要点

要重视，要关注，要努力，要做好，要认真。
"""
        long_source = "主持人：今天讨论了量化投资的核心策略，包括T0策略的中低频长周期预测方法。"
        report = gate.evaluate_text(low_density_note, long_source)
        # 检查 M1 是否在 rule_results 中
        if any(i.rule_id == "M1" for i in _all_issues(report)):
            assert "M1" in report.rule_results, "M1 护栏问题应在 rule_results 中"


def _all_issues(report):
    """从 QualityReport 中提取所有 issues"""
    issues = []
    for rid, result in report.rule_results.items():
        issues.extend(result.issues)
    return issues
