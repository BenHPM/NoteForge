# -*- coding: utf-8 -*-
"""
NoteForge 文件 I/O 工具函数
统一文件读取（UTF-8 → GBK 回退）和错误处理
"""

import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger('noteforge.file_utils')


def safe_read_text(path, encoding_fallback: bool = True) -> Optional[str]:
    """
    安全读取文本文件（UTF-8 回退 GBK/GB2312）

    Args:
        path: 文件路径
        encoding_fallback: 是否尝试 GBK/GB2312 回退

    Returns:
        文件内容，或 None（读取失败）
    """
    encodings = ['utf-8']
    if encoding_fallback:
        encodings.extend(['gbk', 'gb2312'])

    for encoding in encodings:
        try:
            with open(path, 'r', encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.debug(f"读取文件失败 {path}: {e}")
            return None

    logger.debug(f"无法读取文件（编码问题）: {path}")
    return None


def write_file(path, content: str) -> None:
    """
    写入文件（原子写入：先写临时文件再重命名）

    Args:
        path: 目标文件路径
        content: 要写入的内容
    """
    import os
    import tempfile

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # 原子写入：先写临时文件再重命名
    fd, tmp = tempfile.mkstemp(
        suffix='.tmp', dir=str(target.parent),
        prefix=target.stem + '_'
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        # Windows 需要先删除目标文件
        if target.exists():
            target.unlink()
        os.rename(tmp, str(target))
    except Exception:
        # 失败时清理临时文件
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise
