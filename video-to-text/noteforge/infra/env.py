# -*- coding: utf-8 -*-
"""
NoteForge 运行环境检测

惰性检查：不自动执行，由调用方显式调用 check_env()。
CLI 入口（main.py）在解析参数后调用，不干扰 import 和测试。

用法:
    from noteforge.infra.env import check_env
    check_env()  # 检测失败时抛 EnvironmentError
"""
import os
import sys
import logging
from pathlib import Path


def check_env():
    """检测关键依赖是否可用。失败时抛 EnvironmentError（不 sys.exit）。"""
    missing = []

    try:
        import tiktoken  # noqa: F401
    except ImportError:
        missing.append("tiktoken")

    if not missing:
        return

    # 判断是否在 Paraformer 环境中
    exe = Path(sys.executable)
    is_paraformer = "paraformer" in str(exe).lower()

    logger = logging.getLogger('noteforge.env')
    logger.error("缺少依赖: %s", ', '.join(missing))

    if is_paraformer:
        logger.error("你已在 Paraformer 环境中，但依赖仍缺失。请运行: pip install %s", ' '.join(missing))
    else:
        logger.error("当前 Python: %s", sys.executable)
        paraformer_py = Path(__file__).parent.parent.parent / "envs" / "paraformer" / "python.exe"
        logger.error("应使用: %s", paraformer_py)
        logger.error("或使用 noteforge.bat 菜单（自动使用正确环境）")

    raise EnvironmentError(f"缺少依赖: {', '.join(missing)}。请使用 Paraformer 环境（envs/paraformer/python.exe）。")


def get_paraformer_python() -> str:
    """返回 Paraformer 隔离环境的 python.exe 路径。"""
    return str(Path(__file__).parent.parent.parent / "envs" / "paraformer" / "python.exe")
