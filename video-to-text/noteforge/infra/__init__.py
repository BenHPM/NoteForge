# -*- coding: utf-8 -*-
"""NoteForge 基础设施层 — 日志、文件IO、颜色、环境检测"""

from .file_io import read_file, write_file
from .logging_setup import setup_logging
from .colors import RED, GREEN, YELLOW, CYAN, BOLD, RESET, colored
