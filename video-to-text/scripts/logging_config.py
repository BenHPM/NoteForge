# -*- coding: utf-8 -*-
"""NoteForge 统一日志配置"""

import logging
from pathlib import Path

DEFAULT_FORMAT = '%(asctime)s [%(name)s] %(levelname)s: %(message)s'
DEFAULT_LEVEL = logging.INFO

def setup_logging(level=None, log_dir=None, log_file='noteforge.log'):
    """
    统一配置 NoteForge 日志

    - 控制台: StreamHandler with DEFAULT_FORMAT
    - 文件: FileHandler writing to log_dir/log_file (if log_dir provided)
    - 只配置一次（避免重复 handler）
    """
    # Check if already configured (has handlers beyond NullHandler)
    root = logging.getLogger('noteforge')
    if root.handlers:
        return root

    effective_level = level or DEFAULT_LEVEL
    if isinstance(effective_level, str):
        effective_level = getattr(logging, effective_level.upper(), DEFAULT_LEVEL)

    formatter = logging.Formatter(DEFAULT_FORMAT)

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)
    root.setLevel(effective_level)

    # File handler (optional)
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(log_path / log_file), encoding='utf-8')
        fh.setFormatter(formatter)
        root.addHandler(fh)

    return root
