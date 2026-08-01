# -*- coding: utf-8 -*-
"""
Experience Log 生命周期管理测试

覆盖：
  - is_entry_expired: 过期检测
  - is_entry_untriggered: 未触发检测
  - filter_active_entries: 活跃条目过滤
  - prune_experience_log: 清理归档
  - touch_entry: 更新 last_triggered
"""

import pytest
import os
import tempfile
from datetime import datetime, timedelta
from noteforge.quality.experience_lifecycle import (
    is_entry_expired, is_entry_untriggered, filter_active_entries,
    prune_experience_log, touch_entry,
    DEFAULT_TTL_DAYS, DEFAULT_AUTO_ARCHIVE_DAYS,
)


class TestIsEntryExpired:
    """过期检测测试"""

    def test_recent_entry_not_expired(self):
        entry = {'date': datetime.now().strftime('%Y-%m-%d')}
        assert is_entry_expired(entry, ttl_days=90) is False

    def test_old_entry_expired(self):
        old_date = (datetime.now() - timedelta(days=100)).strftime('%Y-%m-%d')
        entry = {'date': old_date}
        assert is_entry_expired(entry, ttl_days=90) is True

    def test_exactly_at_ttl_not_expired(self):
        # 恰好 ttl_days 天，不算过期（> ttl_days 才过期）
        edge_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        entry = {'date': edge_date}
        assert is_entry_expired(entry, ttl_days=90) is False

    def test_one_day_over_ttl_expired(self):
        over_date = (datetime.now() - timedelta(days=91)).strftime('%Y-%m-%d')
        entry = {'date': over_date}
        assert is_entry_expired(entry, ttl_days=90) is True

    def test_no_date_not_expired(self):
        entry = {'id': 'EXP-999'}
        assert is_entry_expired(entry) is False

    def test_invalid_date_not_expired(self):
        entry = {'date': 'not-a-date'}
        assert is_entry_expired(entry) is False


class TestIsEntryUntriggered:
    """未触发检测测试"""

    def test_recently_triggered_not_untriggered(self):
        entry = {
            'date': '2026-01-01',
            'last_triggered': datetime.now().strftime('%Y-%m-%d'),
        }
        assert is_entry_untriggered(entry, prune_days=60) is False

    def test_never_triggered_old_entry_untriggered(self):
        old_date = (datetime.now() - timedelta(days=70)).strftime('%Y-%m-%d')
        entry = {'date': old_date}
        assert is_entry_untriggered(entry, prune_days=60) is True

    def test_recently_created_not_untriggered(self):
        entry = {'date': datetime.now().strftime('%Y-%m-%d')}
        assert is_entry_untriggered(entry, prune_days=60) is False


class TestFilterActiveEntries:
    """活跃条目过滤测试"""

    def test_all_active(self):
        entries = [
            {'id': 'EXP-001', 'date': datetime.now().strftime('%Y-%m-%d')},
            {'id': 'EXP-002', 'date': datetime.now().strftime('%Y-%m-%d')},
        ]
        active = filter_active_entries(entries, ttl_days=90, prune_days=60)
        assert len(active) == 2

    def test_expired_filtered_out(self):
        old_date = (datetime.now() - timedelta(days=100)).strftime('%Y-%m-%d')
        entries = [
            {'id': 'EXP-001', 'date': datetime.now().strftime('%Y-%m-%d')},
            {'id': 'EXP-002', 'date': old_date},
        ]
        active = filter_active_entries(entries, ttl_days=90, prune_days=60)
        assert len(active) == 1
        assert active[0]['id'] == 'EXP-001'

    def test_untriggered_filtered_out(self):
        old_date = (datetime.now() - timedelta(days=70)).strftime('%Y-%m-%d')
        entries = [
            {'id': 'EXP-001', 'date': datetime.now().strftime('%Y-%m-%d')},
            {'id': 'EXP-002', 'date': old_date},  # 旧且未触发
        ]
        active = filter_active_entries(entries, ttl_days=90, prune_days=60)
        assert len(active) == 1

    def test_empty_entries(self):
        active = filter_active_entries([], ttl_days=90, prune_days=60)
        assert active == []


class TestPruneExperienceLog:
    """清理归档测试"""

    def test_prune_archives_old_entries(self):
        """超过 auto_archive_days 的条目应被归档"""
        import yaml
        old_date = (datetime.now() - timedelta(days=200)).strftime('%Y-%m-%d')
        recent_date = datetime.now().strftime('%Y-%m-%d')

        data = {
            'meta': {
                'ttl_days': 90,
                'auto_archive_days': 180,
            },
            'entries': [
                {'id': 'EXP-OLD', 'date': old_date, 'error_type': 'test'},
                {'id': 'EXP-NEW', 'date': recent_date, 'error_type': 'test'},
            ],
        }

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.yaml', delete=False, encoding='utf-8'
        ) as f:
            yaml.dump(data, f, allow_unicode=True)
            tmp_path = f.name

        try:
            stats = prune_experience_log(tmp_path, dry_run=False)
            assert stats['archived'] == 1
            assert stats['active'] == 1
        finally:
            os.unlink(tmp_path)

    def test_prune_dry_run_no_modification(self):
        """dry_run 模式不应修改文件"""
        import yaml
        data = {
            'meta': {'ttl_days': 90, 'auto_archive_days': 180},
            'entries': [
                {'id': 'EXP-001', 'date': '2020-01-01', 'error_type': 'test'},
            ],
        }

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.yaml', delete=False, encoding='utf-8'
        ) as f:
            yaml.dump(data, f, allow_unicode=True)
            tmp_path = f.name

        try:
            stats = prune_experience_log(tmp_path, dry_run=True)
            assert stats['archived'] == 1
            # 文件应未被修改
            with open(tmp_path, 'r', encoding='utf-8') as f:
                content = yaml.safe_load(f)
            assert len(content['entries']) == 1  # 原始数据未变
        finally:
            os.unlink(tmp_path)


class TestTouchEntry:
    """更新 last_triggered 测试"""

    def test_touch_updates_last_triggered(self):
        import yaml
        data = {
            'meta': {},
            'entries': [
                {'id': 'EXP-001', 'date': '2026-01-01', 'error_type': 'test'},
            ],
        }

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.yaml', delete=False, encoding='utf-8'
        ) as f:
            yaml.dump(data, f, allow_unicode=True)
            tmp_path = f.name

        try:
            result = touch_entry(tmp_path, 'EXP-001')
            assert result is True
            # 验证 last_triggered 已更新
            with open(tmp_path, 'r', encoding='utf-8') as f:
                updated = yaml.safe_load(f)
            assert 'last_triggered' in updated['entries'][0]
            assert updated['entries'][0]['last_triggered'] == datetime.now().strftime('%Y-%m-%d')
        finally:
            os.unlink(tmp_path)

    def test_touch_nonexistent_entry(self):
        import yaml
        data = {'meta': {}, 'entries': []}

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.yaml', delete=False, encoding='utf-8'
        ) as f:
            yaml.dump(data, f, allow_unicode=True)
            tmp_path = f.name

        try:
            result = touch_entry(tmp_path, 'EXP-999')
            assert result is False
        finally:
            os.unlink(tmp_path)
