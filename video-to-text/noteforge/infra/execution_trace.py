# -*- coding: utf-8 -*-
"""
NoteForge 流水线执行追踪 — 检查点/断点续传支持

提供 ExecutionTrace 状态机，记录流水线各阶段执行状态，
支持哈希链校验、断点续传、死信标记。

存储路径: output/logs/traces/{trace_id}.json
"""

import json
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional

from noteforge.infra.file_io import read_file, write_file

logger = logging.getLogger('noteforge.infra.execution_trace')

# 默认追踪文件存储目录
DEFAULT_TRACE_DIR = os.path.join('output', 'logs', 'traces')


class ExecutionTrace:
    """流水线执行追踪状态机"""

    class Status(Enum):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        DEAD_LETTER = "dead_letter"

    @dataclass
    class StepRecord:
        """单步执行记录"""
        stage: str  # download/transcribe/preprocess/generate/format/evaluate/sync
        status: 'ExecutionTrace.Status'
        input_hash: str  # SHA-256 of input content
        output_hash: Optional[str] = None  # SHA-256 of output (COMPLETED required)
        started_at: str = ""  # ISO format datetime
        completed_at: Optional[str] = None
        error_type: Optional[str] = None  # TRANSIENT / PERMANENT / DEGRADED
        retry_count: int = 0

        def to_dict(self) -> dict:
            """序列化为字典"""
            d = asdict(self)
            d['status'] = self.status.value
            return d

        @classmethod
        def from_dict(cls, d: dict) -> 'ExecutionTrace.StepRecord':
            """从字典反序列化"""
            d = dict(d)  # shallow copy
            d['status'] = ExecutionTrace.Status(d['status'])
            return cls(**d)

    def __init__(self, trace_dir: Optional[str] = None):
        """
        初始化 ExecutionTrace

        Args:
            trace_dir: 追踪文件存储目录，默认 output/logs/traces/
        """
        self.trace_dir = trace_dir or DEFAULT_TRACE_DIR
        self._last_config_hash: str = ""

    # ── 工具方法 ──

    @staticmethod
    def compute_hash(content: str) -> str:
        """计算 SHA-256 哈希"""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    @staticmethod
    def _now_iso() -> str:
        """当前时间的 ISO 格式字符串"""
        return datetime.now().isoformat()

    def _trace_path(self, trace_id: str) -> str:
        """获取追踪文件路径"""
        return os.path.join(self.trace_dir, f"{trace_id}.json")

    def _ensure_dir(self) -> None:
        """确保追踪目录存在"""
        os.makedirs(self.trace_dir, exist_ok=True)

    # ── 核心方法 ──

    def save(self, trace_id: str, records: List['ExecutionTrace.StepRecord'],
             config_hash: str = "") -> None:
        """
        原子写入追踪文件（write .tmp then rename）

        Args:
            trace_id: 追踪 ID
            records: 步骤记录列表
            config_hash: 引擎配置哈希（可选，用于追踪配置变更）
        """
        self._ensure_dir()
        data = [r.to_dict() for r in records]
        payload: dict = {'steps': data}
        if config_hash:
            payload['config_hash'] = config_hash
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        target_path = self._trace_path(trace_id)

        # 使用 write_file 的原子写入（内部已实现 mkstemp + rename）
        write_file(target_path, content)
        logger.debug(f"追踪文件已保存: {target_path}")

    def resume(self, trace_id: str) -> List['ExecutionTrace.StepRecord']:
        """
        加载追踪文件并校验哈希链

        哈希链校验: step[N].output_hash == step[N+1].input_hash
        若不匹配，从断点处及之后的所有记录标记为 FAILED（status 改为 FAILED，
        output_hash 清空），并自动保存修正后的追踪文件。

        Args:
            trace_id: 追踪 ID

        Returns:
            步骤记录列表（可能为空）
        """
        path = self._trace_path(trace_id)

        if not os.path.exists(path):
            logger.debug(f"追踪文件不存在: {path}")
            return []

        try:
            raw = read_file(path)
        except (ValueError, OSError) as e:
            logger.warning(f"追踪文件读取失败: {path} — {e}")
            return []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"追踪文件 JSON 损坏: {path} — {e}")
            return []

        # 兼容新旧格式：新格式是 dict{'steps': [...], 'config_hash': ...}，旧格式是 list
        if isinstance(data, dict):
            steps_data = data.get('steps', [])
            # 保存 config_hash 供后续 save 时保留
            self._last_config_hash = data.get('config_hash', '')
        elif isinstance(data, list):
            steps_data = data
            self._last_config_hash = ''
        else:
            logger.warning(f"追踪文件格式异常: {path}")
            return []

        # 反序列化
        records: List[ExecutionTrace.StepRecord] = []
        for i, item in enumerate(steps_data):
            try:
                records.append(ExecutionTrace.StepRecord.from_dict(item))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"追踪记录 #{i} 反序列化失败: {e}")
                # 跳过损坏的记录，后续记录的哈希链自然断裂
                continue

        # 哈希链校验
        invalidated = False
        for i in range(len(records) - 1):
            current = records[i]
            next_rec = records[i + 1]

            # DEAD_LETTER 记录是明确标记的永久失败，不参与哈希链校验
            # 遇到 DEAD_LETTER 时停止校验（后续记录已通过级联处理）
            if next_rec.status == ExecutionTrace.Status.DEAD_LETTER:
                break

            # 只有当前步骤 COMPLETED 且有 output_hash 时才校验
            if (current.status == ExecutionTrace.Status.COMPLETED
                    and current.output_hash is not None
                    and current.output_hash != next_rec.input_hash):
                logger.warning(
                    f"哈希链断裂: step[{i}]({current.stage}).output_hash != "
                    f"step[{i+1}]({next_rec.stage}).input_hash — "
                    f"从 {next_rec.stage} 开始失效"
                )
                # 从断点处及之后全部标记为 FAILED（跳过已有的 DEAD_LETTER）
                for j in range(i + 1, len(records)):
                    if records[j].status == ExecutionTrace.Status.DEAD_LETTER:
                        continue
                    records[j].status = ExecutionTrace.Status.FAILED
                    records[j].output_hash = None
                invalidated = True
                break

        if invalidated:
            self.save(trace_id, records, config_hash=self._last_config_hash)
            logger.info(f"追踪文件已修正（哈希链断裂）: {trace_id}")

        return records

    def update_step(self, trace_id: str, stage: str, status: 'ExecutionTrace.Status',
                    **kwargs) -> None:
        """
        更新单个步骤的状态

        如果该 stage 已存在记录，更新其状态和附加字段；
        如果不存在，追加新记录。

        Args:
            trace_id: 追踪 ID
            stage: 阶段名称
            status: 新状态
            **kwargs: 附加字段（input_hash, output_hash, error_type, retry_count 等）
        """
        records = self.resume(trace_id)

        # 查找已有记录
        found = False
        for rec in records:
            if rec.stage == stage:
                rec.status = status
                if 'input_hash' in kwargs:
                    rec.input_hash = kwargs['input_hash']
                if 'output_hash' in kwargs:
                    rec.output_hash = kwargs['output_hash']
                if 'error_type' in kwargs:
                    rec.error_type = kwargs['error_type']
                if 'retry_count' in kwargs:
                    rec.retry_count = kwargs['retry_count']
                if 'started_at' in kwargs:
                    rec.started_at = kwargs['started_at']
                if 'completed_at' in kwargs:
                    rec.completed_at = kwargs['completed_at']
                # RUNNING 时自动填入 started_at
                if status == ExecutionTrace.Status.RUNNING and not rec.started_at:
                    rec.started_at = self._now_iso()
                # COMPLETED 时自动填入 completed_at
                if status == ExecutionTrace.Status.COMPLETED and not rec.completed_at:
                    rec.completed_at = self._now_iso()
                found = True
                break

        if not found:
            # 创建新记录
            new_rec = ExecutionTrace.StepRecord(
                stage=stage,
                status=status,
                input_hash=kwargs.get('input_hash', ''),
                output_hash=kwargs.get('output_hash'),
                started_at=kwargs.get('started_at', ''),
                completed_at=kwargs.get('completed_at'),
                error_type=kwargs.get('error_type'),
                retry_count=kwargs.get('retry_count', 0),
            )
            # 自动填入时间戳
            if status == ExecutionTrace.Status.RUNNING and not new_rec.started_at:
                new_rec.started_at = self._now_iso()
            if status == ExecutionTrace.Status.COMPLETED and not new_rec.completed_at:
                new_rec.completed_at = self._now_iso()
            records.append(new_rec)

        self.save(trace_id, records, config_hash=kwargs.get('config_hash', self._last_config_hash))

    def get_config_hash(self, trace_id: str) -> str:
        """获取追踪文件中保存的 config_hash

        Args:
            trace_id: 追踪 ID

        Returns:
            config_hash 字符串，不存在时返回空字符串
        """
        path = self._trace_path(trace_id)
        if not os.path.exists(path):
            return ""
        try:
            raw = read_file(path)
            data = json.loads(raw)
            if isinstance(data, dict):
                return data.get('config_hash', '')
            return ""
        except (ValueError, OSError, json.JSONDecodeError):
            return ""

    def get_last_completed_stage(self, trace_id: str) -> Optional[str]:
        """
        查找最后一个 COMPLETED 的阶段（用于断点续传）

        Args:
            trace_id: 追踪 ID

        Returns:
            最后完成的阶段名，或 None
        """
        records = self.resume(trace_id)
        for rec in reversed(records):
            if rec.status == ExecutionTrace.Status.COMPLETED:
                return rec.stage
        return None

    def _load_raw(self, trace_id: str) -> List['ExecutionTrace.StepRecord']:
        """
        加载追踪文件（不做哈希链校验）

        用于内部修改操作（mark_dead_letter 等），避免校验逻辑干扰
        已明确设定的状态（如 DEAD_LETTER）。

        Args:
            trace_id: 追踪 ID

        Returns:
            步骤记录列表（可能为空）
        """
        path = self._trace_path(trace_id)

        if not os.path.exists(path):
            return []

        try:
            raw = read_file(path)
        except (ValueError, OSError):
            return []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []

        # 兼容新旧格式
        if isinstance(data, dict):
            steps_data = data.get('steps', [])
            self._last_config_hash = data.get('config_hash', '')
        elif isinstance(data, list):
            steps_data = data
        else:
            return []

        records: List[ExecutionTrace.StepRecord] = []
        for item in steps_data:
            try:
                records.append(ExecutionTrace.StepRecord.from_dict(item))
            except (KeyError, ValueError, TypeError):
                continue

        return records

    def mark_dead_letter(self, trace_id: str, stage: str, error: str) -> None:
        """
        标记为永久失败（死信）

        使用 _load_raw 而非 resume，避免哈希链校验覆盖 DEAD_LETTER 状态。

        Args:
            trace_id: 追踪 ID
            stage: 失败阶段
            error: 错误描述
        """
        records = self._load_raw(trace_id)

        for rec in records:
            if rec.stage == stage:
                rec.status = ExecutionTrace.Status.DEAD_LETTER
                rec.error_type = 'PERMANENT'
                rec.completed_at = self._now_iso()
                break
        else:
            # 阶段不存在，创建新记录
            records.append(ExecutionTrace.StepRecord(
                stage=stage,
                status=ExecutionTrace.Status.DEAD_LETTER,
                input_hash='',
                error_type='PERMANENT',
                completed_at=self._now_iso(),
            ))

        # 将该阶段之后的所有 PENDING/RUNNING 记录也标记为 DEAD_LETTER
        stage_found = False
        for rec in records:
            if rec.stage == stage:
                stage_found = True
                continue
            if stage_found and rec.status in (
                ExecutionTrace.Status.PENDING,
                ExecutionTrace.Status.RUNNING,
            ):
                rec.status = ExecutionTrace.Status.DEAD_LETTER
                rec.error_type = 'PERMANENT'

        self.save(trace_id, records, config_hash=self._last_config_hash)
        logger.info(f"追踪 {trace_id} 阶段 {stage} 已标记为死信: {error}")

    def is_resumable(self, trace_id: str) -> bool:
        """
        检查追踪是否有有效的断点续传点

        条件: 存在至少一个 COMPLETED 阶段，且没有 DEAD_LETTER 阶段

        Args:
            trace_id: 追踪 ID

        Returns:
            是否可续传
        """
        records = self.resume(trace_id)
        if not records:
            return False

        has_completed = False
        for rec in records:
            if rec.status == ExecutionTrace.Status.DEAD_LETTER:
                return False
            if rec.status == ExecutionTrace.Status.COMPLETED:
                has_completed = True

        return has_completed

    def cleanup(self, trace_id: str) -> None:
        """
        删除追踪文件

        Args:
            trace_id: 追踪 ID
        """
        path = self._trace_path(trace_id)
        if os.path.exists(path):
            os.unlink(path)
            logger.debug(f"追踪文件已删除: {path}")
