# -*- coding: utf-8 -*-
"""
NoteForge Experience Log 生命周期管理

P0-3: 防止经验日志无界累积降低 prompt 质量。
- 条目超过 ttl_days 后自动抑制（不注入 prompt）
- 条目超过 auto_archive_days 后归档
- 超过 prune_untriggered_after_days 未触发的条目降权
"""

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

from noteforge.infra.file_io import read_file, write_file

logger = logging.getLogger('noteforge.experience_lifecycle')

# 默认生命周期配置
DEFAULT_TTL_DAYS = 90
DEFAULT_AUTO_ARCHIVE_DAYS = 180
DEFAULT_PRUNE_UNTRIGGERED_DAYS = 60


def load_experience_yaml(path: str) -> dict:
    """加载 experience_log.yaml"""
    import yaml
    content = read_file(path)
    return yaml.safe_load(content) or {}


def save_experience_yaml(path: str, data: dict) -> None:
    """保存 experience_log.yaml"""
    import yaml
    content = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    write_file(path, content)


def is_entry_expired(entry: dict, ttl_days: int = DEFAULT_TTL_DAYS,
                     reference_date: Optional[datetime] = None) -> bool:
    """检查条目是否已过期（超过 ttl_days）

    Args:
        entry: 经验条目（需含 date 字段）
        ttl_days: 生存天数
        reference_date: 参考日期（默认今天）

    Returns:
        True = 已过期，不应注入 prompt
    """
    if reference_date is None:
        reference_date = datetime.now()

    entry_date_str = entry.get('date', '')
    if not entry_date_str:
        return False  # 无日期的条目不过期

    try:
        entry_date = datetime.strptime(entry_date_str, '%Y-%m-%d')
    except ValueError:
        return False

    age_days = (reference_date - entry_date).days
    return age_days > ttl_days


def is_entry_untriggered(entry: dict, prune_days: int = DEFAULT_PRUNE_UNTRIGGERED_DAYS,
                         reference_date: Optional[datetime] = None) -> bool:
    """检查条目是否长期未被触发

    Args:
        entry: 经验条目
        prune_days: 未触发天数阈值
        reference_date: 参考日期

    Returns:
        True = 长期未触发，应降权
    """
    if reference_date is None:
        reference_date = datetime.now()

    # 优先使用 last_triggered，回退到 date
    last_active_str = entry.get('last_triggered', '') or entry.get('date', '')
    if not last_active_str:
        return True  # 无日期视为未触发

    try:
        last_active = datetime.strptime(last_active_str, '%Y-%m-%d')
    except ValueError:
        return True

    inactive_days = (reference_date - last_active).days
    return inactive_days > prune_days


def filter_active_entries(entries: list, ttl_days: int = DEFAULT_TTL_DAYS,
                          prune_days: int = DEFAULT_PRUNE_UNTRIGGERED_DAYS,
                          reference_date: Optional[datetime] = None) -> list:
    """过滤出活跃的条目（未过期 + 未长期未触发 + 内容安全）

    用于 prompt 注入时只包含活跃且安全的条目。

    Args:
        entries: 全部条目列表
        ttl_days: 过期天数
        prune_days: 未触发降权天数
        reference_date: 参考日期

    Returns:
        活跃条目列表（过期、长期未触发、内容不安全的被排除）
    """
    if reference_date is None:
        reference_date = datetime.now()

    active = []
    suppressed = 0
    untriggered = 0
    unsafe = 0

    for entry in entries:
        if is_entry_expired(entry, ttl_days, reference_date):
            suppressed += 1
            continue
        if is_entry_untriggered(entry, prune_days, reference_date):
            untriggered += 1
            continue
        # Risk-5: 内容安全检查（防止注入攻击）
        if not is_entry_safe(entry):
            unsafe += 1
            continue
        active.append(entry)

    if suppressed > 0 or untriggered > 0 or unsafe > 0:
        logger.info(
            f"经验日志过滤: {len(active)} 活跃, "
            f"{suppressed} 过期抑制, {untriggered} 未触发降权, "
            f"{unsafe} 内容不安全"
        )

    return active


# ============================================================
# Risk-5: Experience Log 注入防护
# ============================================================

# 条目内容最大长度（防止超长条目撑爆 prompt）
_MAX_ENTRY_LENGTH = 500

# 危险模式（可能被注入到 prompt 中执行指令）
_INJECTION_PATTERNS = [
    r'ignore\s+(?:previous|above|all)\s+instructions?',
    r'forget\s+(?:previous|above|all)\s+instructions?',
    r'disregard\s+(?:previous|above|all)\s+rules?',
    r'you\s+are\s+now\s+(?:a|an)\s+',
    r'system\s*:\s*',
    r'\<\/?system\>',
    r'```(?:python|bash|sh|javascript|js)\s*\n',  # 代码块注入
]

_INJECTION_RE = re.compile('|'.join(_INJECTION_PATTERNS), re.IGNORECASE)

# 控制字符（除常见空白外）
_CONTROL_CHAR_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def is_entry_safe(entry: dict) -> bool:
    """检查经验条目内容是否安全（防止 prompt 注入攻击）

    检查项：
    1. 条目总长度不超过上限（防止超长条目撑爆 prompt）
    2. 无指令注入模式（如 "ignore previous instructions"）
    3. 无控制字符（除常见空白外的 ASCII 控制字符）
    4. 关键字段存在且类型正确

    Args:
        entry: 经验条目

    Returns:
        True = 安全，可注入 prompt
    """
    # 4. 关键字段存在性检查
    if not isinstance(entry, dict):
        return False
    if not entry.get('id') or not isinstance(entry.get('id'), str):
        return False

    # 1. 长度检查（所有文本字段合计）
    total_length = sum(
        len(str(v)) for k, v in entry.items()
        if isinstance(v, str) and k not in ('id', 'date', 'last_triggered')
    )
    if total_length > _MAX_ENTRY_LENGTH:
        logger.debug(
            f"经验条目 {entry.get('id', '?')} 过长 "
            f"({total_length} > {_MAX_ENTRY_LENGTH})，跳过注入"
        )
        return False

    # 2. 注入模式检查
    entry_text = ' '.join(
        str(v) for v in entry.values() if isinstance(v, str)
    )
    if _INJECTION_RE.search(entry_text):
        logger.warning(
            f"经验条目 {entry.get('id', '?')} 包含疑似注入模式，跳过注入"
        )
        return False

    # 3. 控制字符检查
    if _CONTROL_CHAR_RE.search(entry_text):
        logger.warning(
            f"经验条目 {entry.get('id', '?')} 包含控制字符，跳过注入"
        )
        return False

    return True


def prune_experience_log(path: str, dry_run: bool = False) -> dict:
    """清理经验日志：归档过期条目，更新 last_triggered

    Args:
        path: experience_log.yaml 路径
        dry_run: 只预览不修改

    Returns:
        统计信息 dict
    """
    data = load_experience_yaml(path)
    meta = data.get('meta', {})
    ttl_days = meta.get('ttl_days', DEFAULT_TTL_DAYS)
    auto_archive_days = meta.get('auto_archive_days', DEFAULT_AUTO_ARCHIVE_DAYS)

    entries = data.get('entries', [])
    now = datetime.now()

    active_entries = []
    archived_entries = data.get('archived', [])
    stats = {
        'total': len(entries),
        'active': 0,
        'expired_suppressed': 0,
        'archived': 0,
        'untriggered_pruned': 0,
    }

    for entry in entries:
        age_days = 0
        entry_date_str = entry.get('date', '')
        if entry_date_str:
            try:
                age_days = (now - datetime.strptime(entry_date_str, '%Y-%m-%d')).days
            except ValueError:
                pass

        if age_days > auto_archive_days:
            # 超过归档天数 → 移到 archived
            stats['archived'] += 1
            archived_entries.append(entry)
        elif age_days > ttl_days:
            # 超过 TTL 但未到归档 → 保留但标记为抑制
            entry['_suppressed'] = True
            stats['expired_suppressed'] += 1
            active_entries.append(entry)
        else:
            stats['active'] += 1
            active_entries.append(entry)

    stats['active_total'] = len(active_entries)

    if not dry_run:
        data['entries'] = active_entries
        data['archived'] = archived_entries
        data['meta']['total_entries'] = len(active_entries)
        data['meta']['last_pruned'] = now.strftime('%Y-%m-%d')
        save_experience_yaml(path, data)
        logger.info(f"经验日志清理完成: {stats}")
    else:
        logger.info(f"经验日志清理预览 (dry-run): {stats}")

    return stats


def touch_entry(path: str, entry_id: str) -> bool:
    """更新条目的 last_triggered 日期（当条目被实际使用时调用）

    Args:
        path: experience_log.yaml 路径
        entry_id: 条目 ID（如 "EXP-001"）

    Returns:
        是否成功更新
    """
    data = load_experience_yaml(path)
    entries = data.get('entries', [])

    for entry in entries:
        if entry.get('id') == entry_id:
            entry['last_triggered'] = datetime.now().strftime('%Y-%m-%d')
            save_experience_yaml(path, data)
            return True

    return False
