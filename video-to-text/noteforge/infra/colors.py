# -*- coding: utf-8 -*-
"""NoteForge ANSI 颜色常量（Windows 兼容）"""

# ANSI escape codes
RED = '\033[31m'
GREEN = '\033[32m'
YELLOW = '\033[33m'
CYAN = '\033[36m'
BOLD = '\033[1m'
RESET = '\033[0m'


def colored(text, color):
    """给文本添加 ANSI 颜色"""
    return f"{color}{text}{RESET}"
