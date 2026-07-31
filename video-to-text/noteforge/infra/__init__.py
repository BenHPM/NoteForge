# -*- coding: utf-8 -*-
"""NoteForge 基础设施层 — 日志、文件IO、颜色、环境检测、执行追踪、健康检查、失败策略、断路器、超时守卫"""

from .file_io import read_file, write_file
from .logging_setup import setup_logging
from .colors import RED, GREEN, YELLOW, CYAN, BOLD, RESET, colored
from .execution_trace import ExecutionTrace
from .health_check import run_health_check
from .failure_policy import FailurePolicy, FailureClassifier
from .circuit_breaker import (
    CircuitBreaker,
    TimeoutGuard,
    TIMEOUT_DOWNLOAD,
    TIMEOUT_DOWNLOAD_BATCH,
    TIMEOUT_ASR,
    TIMEOUT_LLM,
    TIMEOUT_QUALITY,
    TIMEOUT_FEISHU,
)
