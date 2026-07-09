# -*- coding: utf-8 -*-
"""
NoteForge Bilibili 批量处理脚本 (bilibili.py) 单元测试

覆盖函数：
  - load_urls
  - load_progress / save_progress
  - process_one
  - main 参数解析与流程

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_bilibili_batch.py -v
"""
import os
os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'

import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open, call
from io import StringIO

import pytest


# ============================================================
# load_urls
# ============================================================

class TestLoadUrls:
    """load_urls 函数测试"""

    def test_basic_url_parsing(self, tmp_path):
        """应正确解析基本 URL"""
        from noteforge.batch.bilibili import load_urls
        url_file = tmp_path / "urls.txt"
        url_file.write_text(
            "https://www.bilibili.com/video/BV1abc123/\n",
            encoding='utf-8'
        )
        result = load_urls(str(url_file))
        assert len(result) == 1
        assert result[0]['url'] == 'https://www.bilibili.com/video/BV1abc123/'
        assert result[0]['category'] == ''

    def test_ignores_hash_comments(self, tmp_path):
        """# 开头的行应作为注释忽略"""
        from noteforge.batch.bilibili import load_urls
        url_file = tmp_path / "urls.txt"
        url_file.write_text(
            "# 这是一个注释\n"
            "https://www.bilibili.com/video/BV1abc/\n"
            "# 另一个注释\n",
            encoding='utf-8'
        )
        result = load_urls(str(url_file))
        assert len(result) == 1

    def test_category_annotation(self, tmp_path):
        """# 不含 | 的行应作为分类名，后续 URL 继承"""
        from noteforge.batch.bilibili import load_urls
        url_file = tmp_path / "urls.txt"
        url_file.write_text(
            "# 量化投资\n"
            "https://www.bilibili.com/video/BV1abc/\n"
            "https://www.bilibili.com/video/BV2def/\n",
            encoding='utf-8'
        )
        result = load_urls(str(url_file))
        assert len(result) == 2
        assert result[0]['category'] == '量化投资'
        assert result[1]['category'] == '量化投资'

    def test_title_annotation_skipped(self, tmp_path):
        """含 | 的分类注释行应被跳过，不作为分类名"""
        from noteforge.batch.bilibili import load_urls
        url_file = tmp_path / "urls.txt"
        url_file.write_text(
            "# 量化投资 | 2021-06-06\n"
            "https://www.bilibili.com/video/BV1abc/\n",
            encoding='utf-8'
        )
        result = load_urls(str(url_file))
        assert len(result) == 1
        assert result[0]['category'] == ''  # 含 | 的是标题注释，不作为分类

    def test_strips_query_parameters(self, tmp_path):
        """URL 中的查询参数应被去掉"""
        from noteforge.batch.bilibili import load_urls
        url_file = tmp_path / "urls.txt"
        url_file.write_text(
            "https://www.bilibili.com/video/BV1abc/?p=2\n",
            encoding='utf-8'
        )
        result = load_urls(str(url_file))
        assert result[0]['url'] == 'https://www.bilibili.com/video/BV1abc/'
        assert '?' not in result[0]['url']

    def test_bv_short_url(self, tmp_path):
        """BV 短链接也应被识别"""
        from noteforge.batch.bilibili import load_urls
        url_file = tmp_path / "urls.txt"
        url_file.write_text("BV1abc123\n", encoding='utf-8')
        result = load_urls(str(url_file))
        assert len(result) == 1
        assert result[0]['url'] == 'BV1abc123'

    def test_multiple_categories(self, tmp_path):
        """多个分类应独立切换"""
        from noteforge.batch.bilibili import load_urls
        url_file = tmp_path / "urls.txt"
        url_file.write_text(
            "# 量化投资\n"
            "https://www.bilibili.com/video/BV1q/\n"
            "# 地缘经济\n"
            "https://www.bilibili.com/video/BV2g/\n"
            "https://www.bilibili.com/video/BV3g/\n",
            encoding='utf-8'
        )
        result = load_urls(str(url_file))
        assert len(result) == 3
        assert result[0]['category'] == '量化投资'
        assert result[1]['category'] == '地缘经济'
        assert result[2]['category'] == '地缘经济'


# ============================================================
# load_progress / save_progress
# ============================================================

class TestBilibiliLoadProgress:
    """bilibili.load_progress 函数测试"""

    def test_file_does_not_exist(self, tmp_path):
        """文件不存在时返回空字典"""
        fake_file = tmp_path / "nonexistent.json"
        with patch('noteforge.batch.bilibili.PROGRESS_FILE', fake_file):
            from noteforge.batch.bilibili import load_progress
            assert load_progress() == {}

    def test_file_exists_returns_dict(self, tmp_path):
        """文件存在时返回解析后的字典"""
        fake_file = tmp_path / "progress.json"
        fake_file.write_text(json.dumps({"url1": {"status": "success"}}), encoding='utf-8')
        with patch('noteforge.batch.bilibili.PROGRESS_FILE', fake_file):
            from noteforge.batch.bilibili import load_progress
            result = load_progress()
            assert result == {"url1": {"status": "success"}}


class TestBilibiliSaveProgress:
    """bilibili.save_progress 函数测试"""

    def test_saves_json_correctly(self, tmp_path):
        """save_progress 应将字典写入 PROGRESS_FILE"""
        fake_file = tmp_path / "progress.json"
        progress = {"url1": {"status": "success", "elapsed": 10.5}}
        with patch('noteforge.batch.bilibili.PROGRESS_FILE', fake_file):
            from noteforge.batch.bilibili import save_progress
            save_progress(progress)
        content = json.loads(fake_file.read_text(encoding='utf-8'))
        assert content == progress


# ============================================================
# process_one
# ============================================================

class TestProcessOne:
    """process_one 函数测试"""

    def test_dry_run_returns_dry_run_status(self):
        """dry_run=True 时应直接返回 dry-run 状态"""
        from noteforge.batch.bilibili import process_one
        result = process_one(
            "https://www.bilibili.com/video/BV1abc/",
            "量化投资",
            dry_run=True
        )
        assert result['status'] == 'dry-run'
        assert result['url'] == "https://www.bilibili.com/video/BV1abc/"

    def test_successful_subprocess(self):
        """成功执行的子进程应返回 success 状态"""
        from noteforge.batch.bilibili import process_one
        mock_result = MagicMock(returncode=0, stdout="output", stderr="")
        with patch('subprocess.run', return_value=mock_result):
            result = process_one("https://www.bilibili.com/video/BV1abc/", "量化投资")
        assert result['status'] == 'success'
        assert 'elapsed' in result

    def test_failed_subprocess(self):
        """失败的子进程应返回 failed 状态"""
        from noteforge.batch.bilibili import process_one
        mock_result = MagicMock(returncode=1, stdout="", stderr="Error occurred")
        with patch('subprocess.run', return_value=mock_result):
            result = process_one("https://www.bilibili.com/video/BV1abc/", "量化投资")
        assert result['status'] == 'failed'
        assert 'error' in result

    def test_timeout_returns_timeout(self):
        """超时应返回 timeout 状态"""
        from noteforge.batch.bilibili import process_one
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd=['sleep'], timeout=30)):
            result = process_one("https://www.bilibili.com/video/BV1abc/", "量化投资")
        assert result['status'] == 'timeout'
        assert result['elapsed'] == 1800

    def test_exception_returns_error(self):
        """异常应返回 error 状态"""
        from noteforge.batch.bilibili import process_one
        with patch('subprocess.run', side_effect=OSError("not found")):
            result = process_one("https://www.bilibili.com/video/BV1abc/", "量化投资")
        assert result['status'] == 'error'
        assert 'not found' in result['error']

    def test_content_type_from_category(self):
        """分类应影响 content_type 参数"""
        from noteforge.batch.bilibili import process_one
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return mock_result

        with patch('subprocess.run', side_effect=fake_run):
            process_one("https://www.bilibili.com/video/BV1abc/", "量化投资")
        args = captured[0]
        assert '--content-type' in args
        idx = args.index('--content-type')
        assert args[idx + 1] == 'interview'

    def test_force_appends_flag(self):
        """force=True 应追加 --force 参数"""
        from noteforge.batch.bilibili import process_one
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return mock_result

        with patch('subprocess.run', side_effect=fake_run):
            process_one("https://www.bilibili.com/video/BV1abc/", "", force=True)
        assert '--force' in captured[0]


# ============================================================
# main 参数解析
# ============================================================

class TestMainArgParsing:
    """main 函数参数解析测试"""

    def test_resume_flag_loads_progress(self, tmp_path, monkeypatch):
        """--resume 应加载进度文件"""
        monkeypatch.setattr('noteforge.batch.bilibili.PROGRESS_FILE', tmp_path / "progress.json")
        fake_urls = tmp_path / "urls.txt"
        fake_urls.write_text("https://www.bilibili.com/video/BV1abc/\n", encoding='utf-8')

        test_argv = ['bilibili', str(fake_urls), '--resume']
        with patch.object(sys, 'argv', test_argv):
            from noteforge.batch.bilibili import main
            with patch('noteforge.batch.bilibili.PROGRESS_FILE', tmp_path / "progress.json"):
                main()

    def test_force_flag(self, tmp_path, monkeypatch):
        """--force 应被正确解析"""
        fake_urls = tmp_path / "urls.txt"
        fake_urls.write_text("https://www.bilibili.com/video/BV1abc/\n", encoding='utf-8')
        monkeypatch.setattr('noteforge.batch.bilibili.PROGRESS_FILE', tmp_path / "progress.json")

        test_argv = ['bilibili', str(fake_urls), '--force']
        with patch.object(sys, 'argv', test_argv):
            from noteforge.batch.bilibili import main
            with patch('noteforge.batch.bilibili.PROGRESS_FILE', tmp_path / "progress.json"):
                with patch('noteforge.batch.bilibili.process_one', return_value={'status': 'dry-run'}):
                    with patch('noteforge.batch.bilibili.save_progress'):
                        main()

    def test_dry_run_flag(self, tmp_path, monkeypatch):
        """--dry-run 应被正确解析，输出 DRY-RUN 模式"""
        fake_urls = tmp_path / "urls.txt"
        fake_urls.write_text("https://www.bilibili.com/video/BV1abc/\n", encoding='utf-8')
        monkeypatch.setattr('noteforge.batch.bilibili.PROGRESS_FILE', tmp_path / "progress.json")

        test_argv = ['bilibili', str(fake_urls), '--dry-run']
        with patch.object(sys, 'argv', test_argv):
            from noteforge.batch.bilibili import main
            with patch('noteforge.batch.bilibili.PROGRESS_FILE', tmp_path / "progress.json"):
                with patch('noteforge.batch.bilibili.process_one', return_value={'status': 'dry-run'}) as mock_proc:
                    with patch('noteforge.batch.bilibili.save_progress'):
                        main()
        mock_proc.assert_called_once()
        # dry_run 应传入 True
        _, kwargs = mock_proc.call_args
        assert kwargs.get('dry_run') is True

    def test_strategy_small_first_default(self, tmp_path, monkeypatch):
        """默认策略应为 small-first"""
        fake_urls = tmp_path / "urls.txt"
        fake_urls.write_text(
            "# A\n"
            "https://www.bilibili.com/video/BV1a/\n"
            "https://www.bilibili.com/video/BV1b/\n"
            "# B\n"
            "https://www.bilibili.com/video/BV2a/\n",
            encoding='utf-8'
        )
        monkeypatch.setattr('noteforge.batch.bilibili.PROGRESS_FILE', tmp_path / "progress.json")

        test_argv = ['bilibili', str(fake_urls)]
        with patch.object(sys, 'argv', test_argv):
            from noteforge.batch.bilibili import main
            with patch('noteforge.batch.bilibili.PROGRESS_FILE', tmp_path / "progress.json"):
                with patch('noteforge.batch.bilibili.process_one', return_value={'status': 'dry-run'}):
                    with patch('noteforge.batch.bilibili.save_progress'):
                        main()

    def test_strategy_category_with_only(self, tmp_path, monkeypatch):
        """--strategy=category 配合 --only 应只处理指定分类"""
        fake_urls = tmp_path / "urls.txt"
        fake_urls.write_text(
            "# 量化投资\n"
            "https://www.bilibili.com/video/BV1a/\n"
            "https://www.bilibili.com/video/BV1b/\n"
            "# 地缘经济\n"
            "https://www.bilibili.com/video/BV2a/\n",
            encoding='utf-8'
        )
        monkeypatch.setattr('noteforge.batch.bilibili.PROGRESS_FILE', tmp_path / "progress.json")

        test_argv = ['bilibili', str(fake_urls), '--strategy', 'category', '--only', '量化投资']
        with patch.object(sys, 'argv', test_argv):
            from noteforge.batch.bilibili import main
            with patch('noteforge.batch.bilibili.PROGRESS_FILE', tmp_path / "progress.json"):
                with patch('noteforge.batch.bilibili.process_one', return_value={'status': 'dry-run'}) as mock_proc:
                    with patch('noteforge.batch.bilibili.save_progress'):
                        main()
        # 只应处理 2 个量化投资的视频
        assert mock_proc.call_count == 2

    def test_max_limit(self, tmp_path, monkeypatch):
        """--max N 应限制处理数量为 N"""
        fake_urls = tmp_path / "urls.txt"
        fake_urls.write_text(
            "# 量化投资\n"
            "https://www.bilibili.com/video/BV1a/\n"
            "https://www.bilibili.com/video/BV1b/\n"
            "https://www.bilibili.com/video/BV1c/\n",
            encoding='utf-8'
        )
        monkeypatch.setattr('noteforge.batch.bilibili.PROGRESS_FILE', tmp_path / "progress.json")

        test_argv = ['bilibili', str(fake_urls), '--max', '2']
        with patch.object(sys, 'argv', test_argv):
            from noteforge.batch.bilibili import main
            with patch('noteforge.batch.bilibili.PROGRESS_FILE', tmp_path / "progress.json"):
                with patch('noteforge.batch.bilibili.process_one', return_value={'status': 'dry-run'}) as mock_proc:
                    with patch('noteforge.batch.bilibili.save_progress'):
                        main()
        assert mock_proc.call_count == 2
