# -*- coding: utf-8 -*-
"""
NoteForge 统一文件 I/O

合并自:
  - file_utils.py (safe_read_text / write_file)
  - llm_note_engine._read_file / _write_file
  - synthesis_engine._read_file / _write_file
  - domain_classifier._read_file
  - external_sync._read_file
  - quality_gate._read_file
  - audio_handler.read_file

统一行为:
  - read_file: UTF-8 → GBK → GB2312 回退，失败抛 ValueError
  - write_file: 原子写入（mkstemp + rename），自动创建父目录
"""

import os
import logging
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger('noteforge.infra.file_io')


def read_file(path, encoding_fallback: bool = True) -> str:
    """
    读取文本文件（UTF-8 → GBK/GB2312 回退）

    Args:
        path: 文件路径
        encoding_fallback: 是否尝试 GBK/GB2312 回退（默认 True）

    Returns:
        文件内容字符串

    Raises:
        ValueError: 所有编码尝试均失败
        FileNotFoundError: 文件不存在
    """
    encodings = ['utf-8']
    if encoding_fallback:
        encodings.extend(['gbk', 'gb2312'])

    last_error = None
    for encoding in encodings:
        try:
            with open(path, 'r', encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            last_error = f"编码问题（尝试: {', '.join(encodings)}）"
            continue
        except FileNotFoundError:
            raise
        except Exception as e:
            logger.debug(f"读取文件失败 {path}: {e}")
            raise

    raise ValueError(f"无法读取文件（{last_error}）: {path}")


def safe_read_text(path, encoding_fallback: bool = True) -> Optional[str]:
    """
    安全读取文本文件（失败返回 None 而非抛异常）

    Args:
        path: 文件路径
        encoding_fallback: 是否尝试 GBK/GB2312 回退

    Returns:
        文件内容，或 None（读取失败）
    """
    try:
        return read_file(path, encoding_fallback=encoding_fallback)
    except (FileNotFoundError, ValueError, OSError) as e:
        logger.debug(f"安全读取失败 {path}: {e}")
        return None


def write_file(path, content: str) -> None:
    """
    写入文件（原子写入：mkstemp + rename）

    Args:
        path: 目标文件路径
        content: 要写入的内容
    """
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
