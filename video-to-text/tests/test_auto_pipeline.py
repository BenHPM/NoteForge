# -*- coding: utf-8 -*-
"""
NoteForge 自主执行流水线 (auto_pipeline) 单元测试

覆盖函数：
  - load_progress / save_progress
  - get_domain_for_category / get_content_type
  - run_cmd
  - catch_up
  - classify_error
  - health_check
  - auto_synthesize
  - _detect_domain_from_name

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_auto_pipeline.py -v
"""
import os
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open, call

import pytest


# ============================================================
# load_progress / save_progress
# ============================================================

class TestLoadProgress:
    """load_progress 函数测试"""

    def test_file_does_not_exist(self, tmp_path):
        """文件不存在时返回空字典"""
        fake_progress = tmp_path / "nonexistent.json"
        with patch('noteforge.batch.auto_pipeline.PROGRESS_FILE', fake_progress):
            from noteforge.batch.auto_pipeline import load_progress
            assert load_progress() == {}

    def test_file_exists_returns_dict(self, tmp_path):
        """文件存在时返回解析后的字典"""
        fake_file = tmp_path / "progress.json"
        fake_file.write_text(json.dumps({"ep01": {"status": "success"}}), encoding='utf-8')
        with patch('noteforge.batch.auto_pipeline.PROGRESS_FILE', fake_file):
            from noteforge.batch.auto_pipeline import load_progress
            result = load_progress()
            assert result == {"ep01": {"status": "success"}}


class TestSaveProgress:
    """save_progress 函数测试"""

    def test_writes_json_to_progress_file(self, tmp_path):
        """save_progress 应将字典写入 PROGRESS_FILE"""
        fake_file = tmp_path / "progress.json"
        progress = {"ep01": {"status": "success"}}
        with patch('noteforge.batch.auto_pipeline.PROGRESS_FILE', fake_file):
            from noteforge.batch.auto_pipeline import save_progress
            save_progress(progress)
        content = json.loads(fake_file.read_text(encoding='utf-8'))
        assert content == progress


# ============================================================
# get_domain_for_category
# ============================================================

class TestGetDomainForCategory:
    """get_domain_for_category 函数测试"""

    def test_known_categories_map_to_domain(self):
        """已知分类应映射到对应的知识域"""
        from noteforge.batch.auto_pipeline import get_domain_for_category
        assert get_domain_for_category('量化投资') == 'finance_investment'
        assert get_domain_for_category('投资') == 'finance_investment'
        assert get_domain_for_category('地缘经济') == 'geoeconomics'
        assert get_domain_for_category('国际分析') == 'intl_analysis'
        assert get_domain_for_category('中国政经') == 'china_politics'
        assert get_domain_for_category('地缘政治') == 'geopolitics'
        assert get_domain_for_category('短视频导演') == 'short_video_directing'

    def test_unknown_category_returns_general(self):
        """未知分类应返回 'general'"""
        from noteforge.batch.auto_pipeline import get_domain_for_category
        assert get_domain_for_category('美食探店') == 'general'
        assert get_domain_for_category('') == 'general'


# ============================================================
# get_content_type
# ============================================================

class TestGetContentType:
    """get_content_type 函数测试"""

    def test_investment_keywords_return_interview(self):
        """含投资/量化/基金关键词的分类应返回 'interview'"""
        from noteforge.batch.auto_pipeline import get_content_type
        assert get_content_type('量化投资') == 'interview'
        assert get_content_type('基金分析') == 'interview'
        assert get_content_type('投资策略') == 'interview'

    def test_other_categories_return_lecture(self):
        """不含投资相关关键词的分类应返回 'lecture'"""
        from noteforge.batch.auto_pipeline import get_content_type
        assert get_content_type('短视频导演') == 'lecture'
        assert get_content_type('地缘经济') == 'lecture'
        assert get_content_type('美食探店') == 'lecture'


# ============================================================
# run_cmd
# ============================================================

class TestRunCmd:
    """run_cmd 函数测试"""

    def test_successful_command(self):
        """成功执行的命令应返回 (0, stdout, '')"""
        from noteforge.batch.auto_pipeline import run_cmd
        mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch('subprocess.run', return_value=mock_result):
            rc, out, err = run_cmd(['echo', 'hi'])
        assert rc == 0
        assert out == "ok"
        assert err == ''

    def test_timeout_returns_negative_one(self):
        """超时应返回 (-1, '', 'TIMEOUT')"""
        from noteforge.batch.auto_pipeline import run_cmd
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='sleep', timeout=5)):
            rc, out, err = run_cmd(['sleep', '10'])
        assert rc == -1
        assert out == ''
        assert err == 'TIMEOUT'

    def test_exception_returns_error_message(self):
        """异常应返回 (-1, '', error_msg)"""
        from noteforge.batch.auto_pipeline import run_cmd
        with patch('subprocess.run', side_effect=OSError("not found")):
            rc, out, err = run_cmd(['nonexistent_command_xyz'])
        assert rc == -1
        assert out == ''
        assert "not found" in err


# ============================================================
# catch_up
# ============================================================

class TestCatchUp:
    """catch_up 函数测试"""

    def test_no_missing_transcripts(self, tmp_path, monkeypatch):
        """无缺失转写时应返回 (0, 0)"""
        transcripts_dir = tmp_path / "transcripts"
        notes_dir = tmp_path / "notes"
        transcripts_dir.mkdir()
        notes_dir.mkdir()

        (transcripts_dir / "ep01.txt").write_text("转写内容", encoding='utf-8')
        (notes_dir / "ep01.md").write_text("# 笔记", encoding='utf-8')

        monkeypatch.setattr('noteforge.batch.auto_pipeline.TRANSCRIPTS_DIR', transcripts_dir)
        monkeypatch.setattr('noteforge.batch.auto_pipeline.NOTES_DIR', notes_dir)

        from noteforge.batch.auto_pipeline import catch_up
        success, failed = catch_up({})
        assert success == 0
        assert failed == 0

    def test_finds_missing_and_processes(self, tmp_path, monkeypatch):
        """找到缺失转写并处理后应返回 (success, failed)"""
        transcripts_dir = tmp_path / "transcripts"
        notes_dir = tmp_path / "notes"
        transcripts_dir.mkdir()
        notes_dir.mkdir()

        (transcripts_dir / "ep01.txt").write_text("转写内容", encoding='utf-8')
        # notes_dir 为空 → ep01 缺失

        monkeypatch.setattr('noteforge.batch.auto_pipeline.TRANSCRIPTS_DIR', transcripts_dir)
        monkeypatch.setattr('noteforge.batch.auto_pipeline.NOTES_DIR', notes_dir)

        progress = {}

        mock_result = MagicMock(error=None, note_path="ep01.md")
        with patch('noteforge.batch.auto_pipeline._create_engine') as mock_factory:
            mock_engine = MagicMock()
            mock_engine.generate_note.return_value = mock_result
            mock_factory.return_value = mock_engine

            from noteforge.batch.auto_pipeline import catch_up
            success, failed = catch_up(progress)

        assert success == 1
        assert failed == 0
        assert 'catchup:ep01' in progress

    def test_handles_generate_note_error(self, tmp_path, monkeypatch):
        """generate_note 返回 error 时计入 failed"""
        transcripts_dir = tmp_path / "transcripts"
        notes_dir = tmp_path / "notes"
        transcripts_dir.mkdir()
        notes_dir.mkdir()

        (transcripts_dir / "ep01.txt").write_text("转写内容", encoding='utf-8')

        monkeypatch.setattr('noteforge.batch.auto_pipeline.TRANSCRIPTS_DIR', transcripts_dir)
        monkeypatch.setattr('noteforge.batch.auto_pipeline.NOTES_DIR', notes_dir)

        mock_result = MagicMock(error="ASR 转写失败")
        with patch('noteforge.batch.auto_pipeline._create_engine') as mock_factory:
            mock_engine = MagicMock()
            mock_engine.generate_note.return_value = mock_result
            mock_factory.return_value = mock_engine

            from noteforge.batch.auto_pipeline import catch_up
            success, failed = catch_up({})

        assert success == 0
        assert failed == 1

    def test_skips_synthesis_knowledge_files(self, tmp_path, monkeypatch):
        """应跳过合成/版本文件"""
        transcripts_dir = tmp_path / "transcripts"
        notes_dir = tmp_path / "notes"
        transcripts_dir.mkdir()
        notes_dir.mkdir()

        (transcripts_dir / "知识体系.txt").write_text("转写", encoding='utf-8')
        (transcripts_dir / "ep01_v2.txt").write_text("转写", encoding='utf-8')
        (transcripts_dir / "ep01_incremental.txt").write_text("转写", encoding='utf-8')

        monkeypatch.setattr('noteforge.batch.auto_pipeline.TRANSCRIPTS_DIR', transcripts_dir)
        monkeypatch.setattr('noteforge.batch.auto_pipeline.NOTES_DIR', notes_dir)

        from noteforge.batch.auto_pipeline import catch_up
        success, failed = catch_up({})
        assert success == 0
        assert failed == 0


# ============================================================
# classify_error
# ============================================================

class TestClassifyError:
    """classify_error 函数测试"""

    def test_timeout_error(self):
        """含 timeout 关键词应分类为 'timeout'"""
        from noteforge.batch.auto_pipeline import classify_error
        assert classify_error("Command timed out after 2400s", "") == 'timeout'

    def test_network_error(self):
        """网络相关错误应分类为 'network'"""
        from noteforge.batch.auto_pipeline import classify_error
        assert classify_error("Connection refused", "") == 'network'
        assert classify_error("getaddrinfo failed", "") == 'network'
        assert classify_error("Connection reset", "") == 'network'

    def test_deleted_video(self):
        """已删除视频应分类为 'deleted'"""
        from noteforge.batch.auto_pipeline import classify_error
        assert classify_error("啥都木有", "") == 'deleted'
        assert classify_error("已删除", "") == 'deleted'
        assert classify_error("No video info found", "") == 'deleted'

    def test_too_short(self):
        """转写过短应分类为 'too_short'"""
        from noteforge.batch.auto_pipeline import classify_error
        assert classify_error("转写文本过短", "") == 'too_short'
        assert classify_error("内容过短，无法生成笔记", "") == 'too_short'

    def test_code_bug(self):
        """Python 内置异常应分类为 'code_bug'"""
        from noteforge.batch.auto_pipeline import classify_error
        assert classify_error("AttributeError: 'NoneType' has no attribute 'x'", "") == 'code_bug'
        assert classify_error("NameError: name 'foo' is not defined", "") == 'code_bug'
        assert classify_error("TypeError: unsupported operand type(s)", "") == 'code_bug'

    def test_unknown_error(self):
        """无法识别的错误应分类为 'unknown'"""
        from noteforge.batch.auto_pipeline import classify_error
        assert classify_error("Some random error", "") == 'unknown'


# ============================================================
# health_check
# ============================================================

class TestHealthCheck:
    """health_check 函数测试"""

    def test_returns_bool(self):
        """health_check 应返回布尔值"""
        from noteforge.batch.auto_pipeline import health_check

        mock_cfg = {
            'provider': {
                'claude': {
                    'base_url': 'http://127.0.0.1:9999'
                }
            }
        }

        with patch('noteforge.batch.auto_pipeline.run_cmd', return_value=(0, "3.10.0", "")):
            with patch('noteforge.batch.auto_pipeline.PROJECT_ROOT', Path('/fake')):
                with patch('noteforge.batch.auto_pipeline.PYTHON', 'python'):
                    with patch('noteforge.batch.auto_pipeline.SYNC_SCRIPT', '/fake/scripts/feishu_sync.py'):
                        # Mock _create_engine to return a valid engine
                        with patch('noteforge.batch.auto_pipeline._create_engine', return_value=MagicMock()):
                            # Mock yaml module so the local import in health_check picks it up
                            mock_yaml = MagicMock()
                            mock_yaml.safe_load.return_value = mock_cfg
                            with patch.dict(sys.modules, {'yaml': mock_yaml}):
                                # Mock Path.exists for config_path and SYNC_SCRIPT
                                with patch.object(Path, 'exists', return_value=True):
                                    # requests.get raises ConnectionError → proxy check = False
                                    with patch('requests.get', side_effect=ConnectionError("refused")):
                                        result = health_check()
        assert isinstance(result, bool)

    def test_checks_five_components(self, capsys):
        """应检查 5 个组件（Python、引擎、配置、代理、飞书）"""
        from noteforge.batch.auto_pipeline import health_check

        mock_cfg = {
            'provider': {
                'claude': {
                    'base_url': 'http://127.0.0.1:9999'
                }
            }
        }

        with patch('noteforge.batch.auto_pipeline.run_cmd', return_value=(0, "3.10.0", "")):
            with patch('noteforge.batch.auto_pipeline.PROJECT_ROOT', Path('/fake')):
                with patch('noteforge.batch.auto_pipeline.PYTHON', 'python'):
                    with patch('noteforge.batch.auto_pipeline.SYNC_SCRIPT', '/fake/scripts/feishu_sync.py'):
                        with patch('noteforge.batch.auto_pipeline._create_engine', return_value=MagicMock()):
                            mock_yaml = MagicMock()
                            mock_yaml.safe_load.return_value = mock_cfg
                            with patch.dict(sys.modules, {'yaml': mock_yaml}):
                                with patch.object(Path, 'exists', return_value=True):
                                    with patch('requests.get', side_effect=ConnectionError("refused")):
                                        health_check()

        captured = capsys.readouterr().out
        assert 'Python 环境' in captured
        assert '引擎初始化' in captured
        assert '配置文件' in captured
        assert 'LLM 代理' in captured
        assert '飞书同步' in captured


# ============================================================
# auto_synthesize
# ============================================================

class TestAutoSynthesize:
    """auto_synthesize 函数测试"""

    def test_no_enough_notes_skips(self, tmp_path, monkeypatch):
        """笔记不足阈值时不触发合成"""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        (notes_dir / "ep01.md").write_text("# 笔记1", encoding='utf-8')

        monkeypatch.setattr('noteforge.batch.auto_pipeline.NOTES_DIR', notes_dir)

        from noteforge.batch.auto_pipeline import auto_synthesize
        count = auto_synthesize({})
        assert count == 0

    def test_enough_notes_calls_synthesis(self, tmp_path, monkeypatch):
        """笔记达到阈值时调用引擎合成 API"""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        for i in range(5):
            (notes_dir / f"量化策略_{i:02d}.md").write_text(f"# 笔记{i}", encoding='utf-8')

        monkeypatch.setattr('noteforge.batch.auto_pipeline.NOTES_DIR', notes_dir)

        mock_result = MagicMock(return_value="/path/to/synthesis.md")
        with patch('noteforge.batch.auto_pipeline._create_engine') as mock_factory:
            mock_engine = MagicMock()
            mock_engine.generate_synthesis_two_stage.return_value = "/path/to/synthesis.md"
            mock_factory.return_value = mock_engine

            from noteforge.batch.auto_pipeline import auto_synthesize
            count = auto_synthesize({})

        assert count == 1
        mock_engine.generate_synthesis_two_stage.assert_called_once_with(domain='finance_investment')


# ============================================================
# main — 集成测试
# ============================================================

class TestMainIntegration:
    """main 函数集成测试"""

    def test_main_resume_loads_progress(self, tmp_path, monkeypatch):
        """--resume 时应加载已有进度"""
        monkeypatch.setattr('noteforge.batch.auto_pipeline.PROGRESS_FILE', tmp_path / "progress.json")
        test_argv = ['auto_pipeline', '--resume']
        with patch.object(sys, 'argv', test_argv):
            with patch('noteforge.batch.auto_pipeline.health_check', return_value=True):
                with patch('noteforge.batch.auto_pipeline.catch_up', return_value=(0, 0)):
                    with patch('noteforge.batch.auto_pipeline.auto_synthesize', return_value=0):
                        with patch('noteforge.batch.auto_pipeline._sync_feishu'):
                            from noteforge.batch.auto_pipeline import main
                            main()

    def test_main_catch_up_mode(self, tmp_path, monkeypatch):
        """--catch-up 模式应只执行补全阶段"""
        monkeypatch.setattr('noteforge.batch.auto_pipeline.PROGRESS_FILE', tmp_path / "progress.json")
        test_argv = ['auto_pipeline', '--catch-up']
        with patch.object(sys, 'argv', test_argv):
            with patch('noteforge.batch.auto_pipeline.health_check', return_value=True):
                with patch('noteforge.batch.auto_pipeline.catch_up', return_value=(1, 0)) as mock_catch:
                    with patch('noteforge.batch.auto_pipeline.auto_synthesize', return_value=0):
                        with patch('noteforge.batch.auto_pipeline._sync_feishu'):
                            from noteforge.batch.auto_pipeline import main
                            main()
        mock_catch.assert_called_once()

    def test_main_synth_only_skips_catchup(self, tmp_path, monkeypatch):
        """--synth-only 应跳过补全和新视频阶段"""
        monkeypatch.setattr('noteforge.batch.auto_pipeline.PROGRESS_FILE', tmp_path / "progress.json")
        test_argv = ['auto_pipeline', '--synth-only']
        with patch.object(sys, 'argv', test_argv):
            with patch('noteforge.batch.auto_pipeline.health_check', return_value=True):
                with patch('noteforge.batch.auto_pipeline.catch_up') as mock_catch:
                    with patch('noteforge.batch.auto_pipeline.auto_synthesize', return_value=0):
                        with patch('noteforge.batch.auto_pipeline._sync_feishu'):
                            from noteforge.batch.auto_pipeline import main
                            main()
        mock_catch.assert_not_called()

    def test_main_no_synth_skips_synthesis(self, tmp_path, monkeypatch):
        """--no-synth 应跳过跨集合成"""
        monkeypatch.setattr('noteforge.batch.auto_pipeline.PROGRESS_FILE', tmp_path / "progress.json")
        test_argv = ['auto_pipeline', '--catch-up', '--no-synth']
        with patch.object(sys, 'argv', test_argv):
            with patch('noteforge.batch.auto_pipeline.health_check', return_value=True):
                with patch('noteforge.batch.auto_pipeline.catch_up', return_value=(0, 0)):
                    with patch('noteforge.batch.auto_pipeline.auto_synthesize') as mock_synth:
                        with patch('noteforge.batch.auto_pipeline._sync_feishu'):
                            from noteforge.batch.auto_pipeline import main
                            main()
        mock_synth.assert_not_called()
