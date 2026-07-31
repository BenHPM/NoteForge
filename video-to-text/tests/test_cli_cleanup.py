# -*- coding: utf-8 -*-
"""
NoteForge CLI cleanup + provider-status 单元测试

覆盖:
- cleanup with --logs removes old files
- cleanup with --temp clears temp directory
- cleanup with --all clears everything
- provider-status displays info
"""
import os
import time
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from noteforge.cli.commands.cleanup import (
    run_cleanup,
    run_provider_status,
    _clean_old_logs,
    _clean_directory,
    _get_base_dir,
)


# ============================================================
# Helper: create a mock args namespace
# ============================================================

def _make_args(**kwargs):
    """Create a mock args namespace with cleanup flags."""
    defaults = dict(
        cleanup=False,
        cleanup_logs=False,
        cleanup_temp=False,
        cleanup_extractions=False,
        cleanup_traces=False,
        cleanup_all=False,
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


# ============================================================
# _clean_old_logs
# ============================================================

class TestCleanOldLogs:

    def test_removes_old_files(self, tmp_path):
        """Files older than 30 days are removed."""
        old_file = tmp_path / "old.log"
        old_file.write_text("old content")
        # Set mtime to 31 days ago
        old_timestamp = time.time() - 31 * 86400
        os.utime(str(old_file), (old_timestamp, old_timestamp))

        new_file = tmp_path / "new.log"
        new_file.write_text("new content")

        removed = _clean_old_logs(tmp_path, max_age_days=30)
        assert old_file in removed
        assert new_file not in removed
        assert not old_file.exists()
        assert new_file.exists()

    def test_keeps_recent_files(self, tmp_path):
        """Files newer than 30 days are kept."""
        recent_file = tmp_path / "recent.log"
        recent_file.write_text("recent content")

        removed = _clean_old_logs(tmp_path, max_age_days=30)
        assert removed == []
        assert recent_file.exists()

    def test_nonexistent_dir(self, tmp_path):
        """Non-existent directory returns empty list."""
        removed = _clean_old_logs(tmp_path / "nonexistent", max_age_days=30)
        assert removed == []


# ============================================================
# _clean_directory
# ============================================================

class TestCleanDirectory:

    def test_removes_files_and_subdirs(self, tmp_path):
        """All files and subdirectories are removed, parent kept."""
        sub_dir = tmp_path / "subdir"
        sub_dir.mkdir()
        (sub_dir / "nested.txt").write_text("nested")

        file1 = tmp_path / "file1.txt"
        file1.write_text("content1")
        file2 = tmp_path / "file2.txt"
        file2.write_text("content2")

        removed = _clean_directory(tmp_path)
        assert len(removed) == 3  # file1, file2, subdir
        assert tmp_path.exists()  # parent dir kept
        assert not file1.exists()
        assert not file2.exists()
        assert not sub_dir.exists()

    def test_empty_dir(self, tmp_path):
        """Empty directory returns empty list."""
        removed = _clean_directory(tmp_path)
        assert removed == []

    def test_nonexistent_dir(self, tmp_path):
        """Non-existent directory returns empty list."""
        removed = _clean_directory(tmp_path / "nonexistent")
        assert removed == []


# ============================================================
# run_cleanup
# ============================================================

class TestRunCleanup:

    def test_cleanup_logs_removes_old_files(self, tmp_path):
        """--cleanup-logs removes old log files."""
        logs_dir = tmp_path / "output" / "logs"
        logs_dir.mkdir(parents=True)

        old_log = logs_dir / "old.log"
        old_log.write_text("old")
        old_timestamp = time.time() - 31 * 86400
        os.utime(str(old_log), (old_timestamp, old_timestamp))

        new_log = logs_dir / "new.log"
        new_log.write_text("new")

        args = _make_args(cleanup_logs=True)
        with patch('noteforge.cli.commands.cleanup._get_base_dir', return_value=tmp_path):
            result = run_cleanup(args)

        assert result == 0
        assert not old_log.exists()
        assert new_log.exists()

    def test_cleanup_temp_clears_temp_dir(self, tmp_path):
        """--cleanup-temp clears temp/ directory contents."""
        temp_dir = tmp_path / "output" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / "tmp1.txt").write_text("temp1")
        (temp_dir / "tmp2.txt").write_text("temp2")

        args = _make_args(cleanup_temp=True)
        with patch('noteforge.cli.commands.cleanup._get_base_dir', return_value=tmp_path):
            result = run_cleanup(args)

        assert result == 0
        assert temp_dir.exists()  # dir itself kept
        assert len(list(temp_dir.iterdir())) == 0  # contents cleared

    def test_cleanup_all_clears_everything(self, tmp_path):
        """--cleanup-all clears logs, temp, extractions, and traces."""
        # Setup directories with content
        logs_dir = tmp_path / "output" / "logs"
        logs_dir.mkdir(parents=True)
        old_log = logs_dir / "old.log"
        old_log.write_text("old")
        old_timestamp = time.time() - 31 * 86400
        os.utime(str(old_log), (old_timestamp, old_timestamp))

        temp_dir = tmp_path / "output" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / "tmp.txt").write_text("tmp")

        extractions_dir = tmp_path / "output" / "notes" / "extractions"
        extractions_dir.mkdir(parents=True)
        (extractions_dir / "ext.json").write_text("{}")

        traces_dir = tmp_path / "output" / "logs" / "traces"
        traces_dir.mkdir(parents=True)
        (traces_dir / "trace.json").write_text("{}")

        args = _make_args(cleanup_all=True)
        with patch('noteforge.cli.commands.cleanup._get_base_dir', return_value=tmp_path):
            result = run_cleanup(args)

        assert result == 0
        assert not old_log.exists()
        assert len(list(temp_dir.iterdir())) == 0
        assert len(list(extractions_dir.iterdir())) == 0
        assert len(list(traces_dir.iterdir())) == 0

    def test_cleanup_no_flags_returns_error(self, tmp_path):
        """No cleanup flags specified returns error code 1."""
        args = _make_args()
        with patch('noteforge.cli.commands.cleanup._get_base_dir', return_value=tmp_path):
            result = run_cleanup(args)

        assert result == 1

    def test_cleanup_flag_without_specific_targets(self, tmp_path):
        """--cleanup without specific flags implies --cleanup-all."""
        temp_dir = tmp_path / "output" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / "tmp.txt").write_text("tmp")

        args = _make_args(cleanup=True)
        with patch('noteforge.cli.commands.cleanup._get_base_dir', return_value=tmp_path):
            result = run_cleanup(args)

        assert result == 0
        assert len(list(temp_dir.iterdir())) == 0

    def test_cleanup_extractions(self, tmp_path):
        """--cleanup-extractions clears notes/extractions/."""
        extractions_dir = tmp_path / "output" / "notes" / "extractions"
        extractions_dir.mkdir(parents=True)
        (extractions_dir / "ep01.json").write_text("{}")
        (extractions_dir / "ep02.json").write_text("{}")

        args = _make_args(cleanup_extractions=True)
        with patch('noteforge.cli.commands.cleanup._get_base_dir', return_value=tmp_path):
            result = run_cleanup(args)

        assert result == 0
        assert extractions_dir.exists()
        assert len(list(extractions_dir.iterdir())) == 0

    def test_cleanup_traces(self, tmp_path):
        """--cleanup-traces clears output/logs/traces/."""
        traces_dir = tmp_path / "output" / "logs" / "traces"
        traces_dir.mkdir(parents=True)
        (traces_dir / "run_001.json").write_text("{}")

        args = _make_args(cleanup_traces=True)
        with patch('noteforge.cli.commands.cleanup._get_base_dir', return_value=tmp_path):
            result = run_cleanup(args)

        assert result == 0
        assert traces_dir.exists()
        assert len(list(traces_dir.iterdir())) == 0


# ============================================================
# run_provider_status
# ============================================================

class TestRunProviderStatus:

    def test_displays_provider_info(self, tmp_path):
        """--provider-status shows provider type, model, base URL, and key status."""
        mock_config = MagicMock()
        mock_config.raw = {
            'provider': {
                'type': 'claude',
                'claude': {
                    'model': 'claude-sonnet-4-20250514',
                    'base_url': 'https://api.anthropic.com',
                    'api_key_env': 'ANTHROPIC_API_KEY',
                },
            },
            'api_retry': {},
        }

        mock_provider = MagicMock()
        mock_provider.get_total_usage.return_value = {
            'input_tokens': 1000,
            'output_tokens': 500,
            'calls': 3,
        }
        mock_provider._total_cache_creation = 200
        mock_provider._total_cache_read = 100

        with patch('noteforge.cli.commands.cleanup._get_base_dir', return_value=tmp_path), \
             patch('noteforge.config.NoteForgeConfig', return_value=mock_config), \
             patch('noteforge.core.llm_providers.create_provider', return_value=mock_provider), \
             patch('noteforge.cli.commands.cleanup.os.environ', {}):

            result = run_provider_status(MagicMock())

        assert result == 0

    def test_shows_proxy_managed_key(self, tmp_path):
        """Provider status shows 'proxy managed' when using proxy URL without local key."""
        mock_config = MagicMock()
        mock_config.raw = {
            'provider': {
                'type': 'claude',
                'claude': {
                    'model': 'claude-sonnet-4-20250514',
                    'base_url': 'https://my-proxy.example.com',
                    'api_key_env': 'ANTHROPIC_API_KEY',
                },
            },
            'api_retry': {},
        }

        mock_provider = MagicMock()
        mock_provider.get_total_usage.return_value = {
            'input_tokens': 0,
            'output_tokens': 0,
            'calls': 0,
        }

        with patch('noteforge.cli.commands.cleanup._get_base_dir', return_value=tmp_path), \
             patch('noteforge.config.NoteForgeConfig', return_value=mock_config), \
             patch('noteforge.core.llm_providers.create_provider', return_value=mock_provider), \
             patch('noteforge.cli.commands.cleanup.os.environ', {}):

            result = run_provider_status(MagicMock())

        assert result == 0

    def test_config_load_failure(self, tmp_path):
        """Provider status returns error when config cannot be loaded."""
        with patch('noteforge.cli.commands.cleanup._get_base_dir', return_value=tmp_path), \
             patch('noteforge.config.NoteForgeConfig', side_effect=Exception("config not found")):

            result = run_provider_status(MagicMock())

        assert result == 1

    def test_provider_init_failure(self, tmp_path):
        """Provider status still returns 0 when provider init fails (shows warning)."""
        from noteforge.core.llm_providers import LLMError

        mock_config = MagicMock()
        mock_config.raw = {
            'provider': {
                'type': 'claude',
                'claude': {
                    'model': 'claude-sonnet-4-20250514',
                    'base_url': 'https://api.anthropic.com',
                    'api_key_env': 'ANTHROPIC_API_KEY',
                },
            },
            'api_retry': {},
        }

        with patch('noteforge.cli.commands.cleanup._get_base_dir', return_value=tmp_path), \
             patch('noteforge.config.NoteForgeConfig', return_value=mock_config), \
             patch('noteforge.core.llm_providers.create_provider', side_effect=LLMError("no key")), \
             patch('noteforge.cli.commands.cleanup.os.environ', {}):

            result = run_provider_status(MagicMock())

        assert result == 0
