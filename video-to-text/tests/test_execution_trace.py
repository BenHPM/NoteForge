# -*- coding: utf-8 -*-
"""
测试 noteforge.infra.execution_trace — ExecutionTrace 状态机

覆盖:
  - 保存与加载基本流程
  - 哈希链校验（有效和无效）
  - 损坏 JSON 恢复
  - get_last_completed_stage
  - mark_dead_letter
  - is_resumable
  - 原子写入（验证成功后无 .tmp 残留）
  - cleanup
  - update_step
  - 空追踪处理

运行:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_execution_trace.py -v
"""

import json
import os
import tempfile
import pytest

from noteforge.infra.execution_trace import ExecutionTrace


class TestSaveAndResume:
    """保存与加载基本流程"""

    def _make_trace(self):
        """创建使用临时目录的 ExecutionTrace"""
        tmp = tempfile.mkdtemp()
        return ExecutionTrace(trace_dir=tmp), tmp

    def test_save_and_resume_roundtrip(self):
        """保存后加载，数据完整一致"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="aaa",
                output_hash="bbb",
                started_at="2026-07-31T10:00:00",
                completed_at="2026-07-31T10:05:00",
            ),
            ExecutionTrace.StepRecord(
                stage="transcribe",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="bbb",
                output_hash="ccc",
                started_at="2026-07-31T10:05:00",
                completed_at="2026-07-31T10:15:00",
            ),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        assert len(loaded) == 2
        assert loaded[0].stage == "download"
        assert loaded[0].status == ExecutionTrace.Status.COMPLETED
        assert loaded[0].input_hash == "aaa"
        assert loaded[0].output_hash == "bbb"
        assert loaded[1].stage == "transcribe"
        assert loaded[1].status == ExecutionTrace.Status.COMPLETED
        assert loaded[1].input_hash == "bbb"
        assert loaded[1].output_hash == "ccc"

    def test_resume_nonexistent_trace(self):
        """加载不存在的追踪返回空列表"""
        trace, tmp = self._make_trace()
        loaded = trace.resume("nonexistent")
        assert loaded == []

    def test_save_creates_directory(self):
        """保存时自动创建目录"""
        tmp = tempfile.mkdtemp()
        trace_dir = os.path.join(tmp, "deep", "nested", "traces")
        trace = ExecutionTrace(trace_dir=trace_dir)
        records = [
            ExecutionTrace.StepRecord(
                stage="download",
                status=ExecutionTrace.Status.PENDING,
                input_hash="abc",
            )
        ]
        trace.save("ep01", records)
        assert os.path.exists(os.path.join(trace_dir, "ep01.json"))


class TestHashChainValidation:
    """哈希链校验"""

    def _make_trace(self):
        tmp = tempfile.mkdtemp()
        return ExecutionTrace(trace_dir=tmp), tmp

    def test_valid_hash_chain(self):
        """有效哈希链：step[N].output_hash == step[N+1].input_hash"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1",
                output_hash="h2",
                started_at="2026-07-31T10:00:00",
                completed_at="2026-07-31T10:05:00",
            ),
            ExecutionTrace.StepRecord(
                stage="transcribe",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h2",  # 匹配前一步 output_hash
                output_hash="h3",
                started_at="2026-07-31T10:05:00",
                completed_at="2026-07-31T10:15:00",
            ),
            ExecutionTrace.StepRecord(
                stage="generate",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h3",  # 匹配前一步 output_hash
                output_hash="h4",
                started_at="2026-07-31T10:15:00",
                completed_at="2026-07-31T10:30:00",
            ),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        # 全部保持 COMPLETED
        assert all(r.status == ExecutionTrace.Status.COMPLETED for r in loaded)

    def test_invalid_hash_chain_invalidates_from_mismatch(self):
        """哈希链断裂：从断点处及之后标记为 FAILED"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1",
                output_hash="h2",
                started_at="2026-07-31T10:00:00",
                completed_at="2026-07-31T10:05:00",
            ),
            ExecutionTrace.StepRecord(
                stage="transcribe",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="MISMATCH",  # 与前一步 output_hash 不匹配
                output_hash="h3",
                started_at="2026-07-31T10:05:00",
                completed_at="2026-07-31T10:15:00",
            ),
            ExecutionTrace.StepRecord(
                stage="generate",
                status=ExecutionTrace.Status.COMPLETED,
                input_hash="h3",
                output_hash="h4",
                started_at="2026-07-31T10:15:00",
                completed_at="2026-07-31T10:30:00",
            ),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        # download 仍为 COMPLETED
        assert loaded[0].status == ExecutionTrace.Status.COMPLETED
        # transcribe 和 generate 被标记为 FAILED
        assert loaded[1].status == ExecutionTrace.Status.FAILED
        assert loaded[1].output_hash is None
        assert loaded[2].status == ExecutionTrace.Status.FAILED
        assert loaded[2].output_hash is None

    def test_hash_chain_skip_non_completed(self):
        """非 COMPLETED 步骤不触发哈希链校验"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download",
                status=ExecutionTrace.Status.FAILED,  # 非 COMPLETED
                input_hash="h1",
                output_hash="h2",
                started_at="2026-07-31T10:00:00",
                error_type="TRANSIENT",
            ),
            ExecutionTrace.StepRecord(
                stage="transcribe",
                status=ExecutionTrace.Status.PENDING,
                input_hash="DIFFERENT",  # 不校验，因为前一步不是 COMPLETED
            ),
        ]
        trace.save("ep01", records)
        loaded = trace.resume("ep01")

        # 状态不变
        assert loaded[0].status == ExecutionTrace.Status.FAILED
        assert loaded[1].status == ExecutionTrace.Status.PENDING


class TestCorruptedJsonRecovery:
    """损坏 JSON 恢复"""

    def _make_trace(self):
        tmp = tempfile.mkdtemp()
        return ExecutionTrace(trace_dir=tmp), tmp

    def test_truncated_json_returns_empty(self):
        """截断的 JSON 文件返回空列表"""
        trace, tmp = self._make_trace()
        path = os.path.join(tmp, "ep01.json")
        with open(path, 'w', encoding='utf-8') as f:
            f.write('[{"stage": "download", "status": "completed", "input_')
        loaded = trace.resume("ep01")
        assert loaded == []

    def test_invalid_json_returns_empty(self):
        """完全无效的 JSON 返回空列表"""
        trace, tmp = self._make_trace()
        path = os.path.join(tmp, "ep01.json")
        with open(path, 'w', encoding='utf-8') as f:
            f.write('this is not json at all')
        loaded = trace.resume("ep01")
        assert loaded == []

    def test_non_list_json_returns_empty(self):
        """JSON 不是列表时返回空列表"""
        trace, tmp = self._make_trace()
        path = os.path.join(tmp, "ep01.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({"not": "a list"}, f)
        loaded = trace.resume("ep01")
        assert loaded == []

    def test_partial_corrupt_records_skipped(self):
        """部分损坏的记录被跳过，其余保留"""
        trace, tmp = self._make_trace()
        path = os.path.join(tmp, "ep01.json")
        data = [
            {"stage": "download", "status": "completed", "input_hash": "h1"},
            {"bad": "record"},  # 缺少必要字段
            {"stage": "generate", "status": "pending", "input_hash": "h2"},
        ]
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        loaded = trace.resume("ep01")
        # 损坏记录被跳过
        assert len(loaded) == 2
        assert loaded[0].stage == "download"
        assert loaded[1].stage == "generate"


class TestGetLastCompletedStage:
    """get_last_completed_stage 测试"""

    def _make_trace(self):
        tmp = tempfile.mkdtemp()
        return ExecutionTrace(trace_dir=tmp), tmp

    def test_returns_last_completed(self):
        """返回最后一个 COMPLETED 阶段"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1", output_hash="h2",
            ),
            ExecutionTrace.StepRecord(
                stage="transcribe", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h2", output_hash="h3",
            ),
            ExecutionTrace.StepRecord(
                stage="generate", status=ExecutionTrace.Status.RUNNING,
                input_hash="h3",
            ),
        ]
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") == "transcribe"

    def test_no_completed_returns_none(self):
        """无 COMPLETED 阶段返回 None"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.RUNNING,
                input_hash="h1",
            ),
        ]
        trace.save("ep01", records)
        assert trace.get_last_completed_stage("ep01") is None

    def test_empty_trace_returns_none(self):
        """空追踪返回 None"""
        trace, tmp = self._make_trace()
        assert trace.get_last_completed_stage("nonexistent") is None


class TestMarkDeadLetter:
    """mark_dead_letter 测试"""

    def _make_trace(self):
        tmp = tempfile.mkdtemp()
        return ExecutionTrace(trace_dir=tmp), tmp

    def test_mark_existing_stage_dead_letter(self):
        """标记已有阶段为死信"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1", output_hash="h2",
            ),
            ExecutionTrace.StepRecord(
                stage="transcribe", status=ExecutionTrace.Status.FAILED,
                input_hash="h2", error_type="TRANSIENT", retry_count=3,
            ),
        ]
        trace.save("ep01", records)
        trace.mark_dead_letter("ep01", "transcribe", "Max retries exceeded")

        loaded = trace.resume("ep01")
        assert loaded[1].status == ExecutionTrace.Status.DEAD_LETTER
        assert loaded[1].error_type == "PERMANENT"
        assert loaded[1].completed_at is not None

    def test_mark_dead_letter_cascades_to_pending(self):
        """死信标记级联到后续 PENDING/RUNNING 阶段"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1", output_hash="h2",
            ),
            ExecutionTrace.StepRecord(
                stage="transcribe", status=ExecutionTrace.Status.FAILED,
                input_hash="h2", error_type="TRANSIENT",
            ),
            ExecutionTrace.StepRecord(
                stage="generate", status=ExecutionTrace.Status.PENDING,
                input_hash="h3",
            ),
            ExecutionTrace.StepRecord(
                stage="format", status=ExecutionTrace.Status.RUNNING,
                input_hash="h4",
            ),
        ]
        trace.save("ep01", records)
        trace.mark_dead_letter("ep01", "transcribe", "Unrecoverable error")

        loaded = trace.resume("ep01")
        # transcribe → DEAD_LETTER
        assert loaded[1].status == ExecutionTrace.Status.DEAD_LETTER
        # generate (PENDING) → DEAD_LETTER
        assert loaded[2].status == ExecutionTrace.Status.DEAD_LETTER
        assert loaded[2].error_type == "PERMANENT"
        # format (RUNNING) → DEAD_LETTER
        assert loaded[3].status == ExecutionTrace.Status.DEAD_LETTER
        assert loaded[3].error_type == "PERMANENT"
        # download (COMPLETED) 不受影响
        assert loaded[0].status == ExecutionTrace.Status.COMPLETED

    def test_mark_dead_letter_new_stage(self):
        """标记不存在的阶段为死信（创建新记录）"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1", output_hash="h2",
            ),
        ]
        trace.save("ep01", records)
        trace.mark_dead_letter("ep01", "sync", "Auth failure")

        loaded = trace.resume("ep01")
        assert len(loaded) == 2
        assert loaded[1].stage == "sync"
        assert loaded[1].status == ExecutionTrace.Status.DEAD_LETTER


class TestIsResumable:
    """is_resumable 测试"""

    def _make_trace(self):
        tmp = tempfile.mkdtemp()
        return ExecutionTrace(trace_dir=tmp), tmp

    def test_resumable_with_completed(self):
        """有 COMPLETED 且无 DEAD_LETTER → 可续传"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1", output_hash="h2",
            ),
            ExecutionTrace.StepRecord(
                stage="transcribe", status=ExecutionTrace.Status.FAILED,
                input_hash="h2", error_type="TRANSIENT",
            ),
        ]
        trace.save("ep01", records)
        assert trace.is_resumable("ep01") is True

    def test_not_resumable_with_dead_letter(self):
        """有 DEAD_LETTER → 不可续传"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1", output_hash="h2",
            ),
            ExecutionTrace.StepRecord(
                stage="transcribe", status=ExecutionTrace.Status.DEAD_LETTER,
                input_hash="h2", error_type="PERMANENT",
            ),
        ]
        trace.save("ep01", records)
        assert trace.is_resumable("ep01") is False

    def test_not_resumable_no_completed(self):
        """无 COMPLETED → 不可续传"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.RUNNING,
                input_hash="h1",
            ),
        ]
        trace.save("ep01", records)
        assert trace.is_resumable("ep01") is False

    def test_not_resumable_empty_trace(self):
        """空追踪 → 不可续传"""
        trace, tmp = self._make_trace()
        assert trace.is_resumable("nonexistent") is False


class TestAtomicWrite:
    """原子写入测试"""

    def _make_trace(self):
        tmp = tempfile.mkdtemp()
        return ExecutionTrace(trace_dir=tmp), tmp

    def test_no_tmp_left_after_success(self):
        """成功写入后无 .tmp 残留"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1", output_hash="h2",
            ),
        ]
        trace.save("ep01", records)

        # 检查无 .tmp 文件
        tmp_files = [f for f in os.listdir(tmp) if f.endswith('.tmp')]
        assert len(tmp_files) == 0

        # 目标文件存在
        assert os.path.exists(os.path.join(tmp, "ep01.json"))

    def test_overwrite_existing_trace(self):
        """覆盖已有追踪文件"""
        trace, tmp = self._make_trace()

        # 第一次保存
        records_v1 = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1", output_hash="h2",
            ),
        ]
        trace.save("ep01", records_v1)

        # 第二次保存（覆盖）
        records_v2 = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1", output_hash="h2",
            ),
            ExecutionTrace.StepRecord(
                stage="transcribe", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h2", output_hash="h3",
            ),
        ]
        trace.save("ep01", records_v2)

        loaded = trace.resume("ep01")
        assert len(loaded) == 2


class TestCleanup:
    """cleanup 测试"""

    def _make_trace(self):
        tmp = tempfile.mkdtemp()
        return ExecutionTrace(trace_dir=tmp), tmp

    def test_cleanup_removes_file(self):
        """cleanup 删除追踪文件"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1", output_hash="h2",
            ),
        ]
        trace.save("ep01", records)
        assert os.path.exists(os.path.join(tmp, "ep01.json"))

        trace.cleanup("ep01")
        assert not os.path.exists(os.path.join(tmp, "ep01.json"))

    def test_cleanup_nonexistent_no_error(self):
        """cleanup 不存在的追踪不报错"""
        trace, tmp = self._make_trace()
        trace.cleanup("nonexistent")  # 不应抛异常


class TestUpdateStep:
    """update_step 测试"""

    def _make_trace(self):
        tmp = tempfile.mkdtemp()
        return ExecutionTrace(trace_dir=tmp), tmp

    def test_update_existing_step(self):
        """更新已有步骤的状态"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.RUNNING,
                input_hash="h1", started_at="2026-07-31T10:00:00",
            ),
        ]
        trace.save("ep01", records)

        trace.update_step("ep01", "download", ExecutionTrace.Status.COMPLETED,
                          output_hash="h2")

        loaded = trace.resume("ep01")
        assert len(loaded) == 1
        assert loaded[0].status == ExecutionTrace.Status.COMPLETED
        assert loaded[0].output_hash == "h2"
        assert loaded[0].completed_at is not None

    def test_update_new_step(self):
        """追加新步骤"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="download", status=ExecutionTrace.Status.COMPLETED,
                input_hash="h1", output_hash="h2",
            ),
        ]
        trace.save("ep01", records)

        trace.update_step("ep01", "transcribe", ExecutionTrace.Status.RUNNING,
                          input_hash="h2")

        loaded = trace.resume("ep01")
        assert len(loaded) == 2
        assert loaded[1].stage == "transcribe"
        assert loaded[1].status == ExecutionTrace.Status.RUNNING
        assert loaded[1].input_hash == "h2"
        assert loaded[1].started_at != ""

    def test_update_step_with_retry_count(self):
        """更新步骤的 retry_count"""
        trace, tmp = self._make_trace()
        records = [
            ExecutionTrace.StepRecord(
                stage="transcribe", status=ExecutionTrace.Status.FAILED,
                input_hash="h2", error_type="TRANSIENT", retry_count=1,
            ),
        ]
        trace.save("ep01", records)

        trace.update_step("ep01", "transcribe", ExecutionTrace.Status.RUNNING,
                          retry_count=2)

        loaded = trace.resume("ep01")
        assert loaded[0].retry_count == 2
        assert loaded[0].status == ExecutionTrace.Status.RUNNING

    def test_update_step_on_empty_trace(self):
        """在空追踪上 update_step 创建新记录"""
        trace, tmp = self._make_trace()
        trace.update_step("ep01", "download", ExecutionTrace.Status.RUNNING,
                          input_hash="h1")

        loaded = trace.resume("ep01")
        assert len(loaded) == 1
        assert loaded[0].stage == "download"
        assert loaded[0].status == ExecutionTrace.Status.RUNNING
        assert loaded[0].input_hash == "h1"


class TestEmptyTraceHandling:
    """空追踪处理"""

    def _make_trace(self):
        tmp = tempfile.mkdtemp()
        return ExecutionTrace(trace_dir=tmp), tmp

    def test_save_empty_records(self):
        """保存空记录列表"""
        trace, tmp = self._make_trace()
        trace.save("ep01", [])
        loaded = trace.resume("ep01")
        assert loaded == []

    def test_resume_empty_trace_file(self):
        """加载空 JSON 数组"""
        trace, tmp = self._make_trace()
        path = os.path.join(tmp, "ep01.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump([], f)
        loaded = trace.resume("ep01")
        assert loaded == []

    def test_compute_hash_deterministic(self):
        """compute_hash 结果确定性"""
        h1 = ExecutionTrace.compute_hash("hello world")
        h2 = ExecutionTrace.compute_hash("hello world")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest

    def test_compute_hash_different_inputs(self):
        """不同输入产生不同哈希"""
        h1 = ExecutionTrace.compute_hash("input A")
        h2 = ExecutionTrace.compute_hash("input B")
        assert h1 != h2
