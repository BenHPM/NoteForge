# -*- coding: utf-8 -*-
"""
NoteForge CLI 知识域命令单元测试

覆盖 noteforge/cli/commands/domain.py 的核心函数：
  - run_detect_domain: 检测文件所属知识域
  - run_domain_list: 列出所有已配置知识域
  - run_incremental_update: 按域增量合成更新

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_cli_domain.py -v
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
import pytest

from noteforge.cli.commands.domain import (
    run_detect_domain,
    run_domain_list,
    run_incremental_update,
)


# ============================================================
# Helpers
# ============================================================

_TEST_DOMAINS = [
    {
        'id': 'finance_investment',
        'name': '金融投资',
        'output_name': '金融投资-知识体系',
        'match_keywords': ['量化', '基金', '因子', 'ROE'],
        'exclude_keywords': ['导演', '短视频'],
        'match_files': ['*量化*', '*基金*'],
    },
    {
        'id': 'general',
        'name': '其他',
        'output_name': '其他笔记-知识体系',
        'match_keywords': [],
        'match_files': [],
    },
]


def _make_engine(tmp_path):
    """Create a MagicMock engine with domain-related attributes."""
    eng = MagicMock()
    eng.base_dir = tmp_path
    eng.notes_dir = tmp_path / "notes"
    eng.notes_dir.mkdir(parents=True, exist_ok=True)
    eng.token_manager = MagicMock()
    eng.detect_domain = MagicMock(return_value='finance_investment')
    eng.get_domain_config = MagicMock(return_value={
        'id': 'finance_investment',
        'name': '金融投资',
        'output_name': '金融投资-知识体系',
        'match_keywords': ['量化', '基金', '因子', 'ROE'],
        'exclude_keywords': ['导演', '短视频'],
        'match_files': ['*量化*', '*基金*'],
    })
    eng.get_notes_by_domain = MagicMock(return_value={
        'finance_investment': [str(tmp_path / "notes" / "ep01_量化投资.md")],
    })
    eng.update_synthesis_incremental = MagicMock(return_value=str(tmp_path / "notes" / "金融投资-知识体系.md"))
    return eng


def _make_args(**overrides):
    """Create a MagicMock args object with sensible defaults."""
    defaults = dict(
        detect_domain='',
        domain_list=False,
        domain=None,
        provider=None,
        format='table',
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def _mock_load_domains(domains, base_dir=None):
    """Return a mock _load_domains that returns (domains, config_mgr)."""
    mock_config_mgr = MagicMock()
    mock_config_mgr.raw = {'knowledge_domains': domains}
    mock_config_mgr.path_config = MagicMock()
    mock_config_mgr.path_config.base_dir = base_dir or Path.cwd()
    return MagicMock(return_value=(domains, mock_config_mgr))


# ============================================================
# Test: run_detect_domain
# ============================================================

class TestDetectDomain:
    """run_detect_domain 函数测试"""

    def test_nonexistent_file_returns_error(self, tmp_path):
        """文件不存在时返回错误码 1"""
        args = _make_args(detect_domain=str(tmp_path / "nonexistent.md"))
        result = run_detect_domain(args)
        assert result == 1

    def test_detects_domain_for_existing_file(self, tmp_path, capsys):
        """对存在的文件检测知识域，输出域信息"""
        note_file = tmp_path / "notes" / "ep01_量化投资策略.md"
        note_file.parent.mkdir(parents=True, exist_ok=True)
        note_file.write_text("# 量化投资策略\n\n因子投资和ROE分析", encoding='utf-8')

        args = _make_args(detect_domain=str(note_file))

        with patch('noteforge.cli.commands.domain._load_domains',
                   _mock_load_domains(_TEST_DOMAINS, tmp_path)):
            result = run_detect_domain(args)

        assert result == 0
        captured = capsys.readouterr()
        assert 'finance_investment' in captured.out

    def test_general_domain_for_unmatched_file(self, tmp_path, capsys):
        """无匹配关键词的文件归入 general 域"""
        note_file = tmp_path / "notes" / "random_notes.md"
        note_file.parent.mkdir(parents=True, exist_ok=True)
        note_file.write_text("# 随便写写\n\n一些无关内容", encoding='utf-8')

        args = _make_args(detect_domain=str(note_file))

        with patch('noteforge.cli.commands.domain._load_domains',
                   _mock_load_domains(_TEST_DOMAINS, tmp_path)):
            result = run_detect_domain(args)

        assert result == 0
        captured = capsys.readouterr()
        assert 'general' in captured.out


# ============================================================
# Test: run_domain_list
# ============================================================

class TestDomainList:
    """run_domain_list 函数测试"""

    def test_empty_domains(self, tmp_path, capsys):
        """无知识域配置时输出提示"""
        args = _make_args(domain_list=True)

        with patch('noteforge.cli.commands.domain._load_domains',
                   _mock_load_domains([])):
            result = run_domain_list(args)

        assert result == 0
        captured = capsys.readouterr()
        assert '未配置知识域' in captured.out

    def test_lists_configured_domains_table(self, tmp_path, capsys):
        """表格格式列出知识域"""
        args = _make_args(domain_list=True, format='table')

        with patch('noteforge.cli.commands.domain._load_domains',
                   _mock_load_domains(_TEST_DOMAINS)):
            result = run_domain_list(args)

        assert result == 0
        captured = capsys.readouterr()
        assert 'finance_investment' in captured.out
        assert 'general' in captured.out

    def test_lists_configured_domains_json(self, tmp_path, capsys):
        """JSON 格式列出知识域"""
        args = _make_args(domain_list=True, format='json')

        with patch('noteforge.cli.commands.domain._load_domains',
                   _mock_load_domains(_TEST_DOMAINS)):
            result = run_domain_list(args)

        assert result == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed[0]['id'] == 'finance_investment'


# ============================================================
# Test: run_incremental_update
# ============================================================

class TestIncrementalUpdate:
    """run_incremental_update 函数测试"""

    def test_no_domain_returns_error(self, tmp_path):
        """未指定 --domain 时返回错误码 1"""
        engine = _make_engine(tmp_path)
        args = _make_args(domain=None)
        result = run_incremental_update(engine, args)
        assert result == 1

    def test_unknown_domain_returns_error(self, tmp_path):
        """指定不存在的知识域时返回错误码 1"""
        engine = _make_engine(tmp_path)
        engine.get_domain_config = MagicMock(return_value={
            'id': 'general',
            'name': '其他',
            'output_name': '其他笔记-知识体系',
        })
        args = _make_args(domain='nonexistent_domain')
        result = run_incremental_update(engine, args)
        assert result == 1

    def test_domain_with_no_notes_returns_early(self, tmp_path, capsys):
        """域内无笔记时提前返回 0"""
        engine = _make_engine(tmp_path)
        engine.get_notes_by_domain = MagicMock(return_value={'finance_investment': []})
        args = _make_args(domain='finance_investment')
        result = run_incremental_update(engine, args)
        assert result == 0
        captured = capsys.readouterr()
        assert '没有笔记' in captured.out

    def test_incremental_update_calls_engine(self, tmp_path, capsys):
        """有笔记时调用 engine.update_synthesis_incremental"""
        engine = _make_engine(tmp_path)
        # Create a real note file so os.path.getmtime works
        note_path = tmp_path / "notes" / "ep01_量化投资.md"
        note_path.write_text("# test", encoding='utf-8')
        engine.get_notes_by_domain = MagicMock(return_value={
            'finance_investment': [str(note_path)],
        })
        args = _make_args(domain='finance_investment')
        result = run_incremental_update(engine, args)
        assert result == 0
        engine.update_synthesis_incremental.assert_called_once()
        captured = capsys.readouterr()
        assert '增量更新完成' in captured.out

    def test_incremental_update_failure_returns_error(self, tmp_path, capsys):
        """增量更新失败时返回错误码 1"""
        engine = _make_engine(tmp_path)
        note_path = tmp_path / "notes" / "ep01_量化投资.md"
        note_path.write_text("# test", encoding='utf-8')
        engine.get_notes_by_domain = MagicMock(return_value={
            'finance_investment': [str(note_path)],
        })
        engine.update_synthesis_incremental = MagicMock(return_value=None)
        args = _make_args(domain='finance_investment')
        result = run_incremental_update(engine, args)
        assert result == 1
        captured = capsys.readouterr()
        assert '增量更新失败' in captured.out
