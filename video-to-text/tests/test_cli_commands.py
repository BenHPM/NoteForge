# -*- coding: utf-8 -*-
"""
NoteForge CLI 命令执行逻辑单元测试

覆盖 noteforge/cli/commands.py 的核心函数：
  - _show_cached_quality / run_check_only / run_search / run_list_notes
  - run_youtube / run_youtube_playlist / run_bilibili / run_audio_url
  - run_synthesis / run_synthesis_2stage / run_synthesis_incremental
  - run_podcast_subscribe / run_batch / run_single_note

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_cli_commands.py -v
"""
import os
os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


# ============================================================
# Helpers
# ============================================================

class FakeResult:
    """Minimal stand-in for GenerationResult with correct __bool__/__eq__."""

    def __init__(self, error=None, note_path='', total_score=0,
                 overall_passed=False):
        self.error = error
        self.note_path = note_path
        self.total_score = total_score
        self.overall_passed = overall_passed


def _make_engine(tmp_path):
    """Create a MagicMock engine with all attributes commands.py uses."""
    eng = MagicMock()
    eng.base_dir = tmp_path
    eng.transcripts_dir = tmp_path / "transcripts"
    eng.notes_dir = tmp_path / "notes"
    eng.transcripts_dir.mkdir(parents=True, exist_ok=True)
    eng.notes_dir.mkdir(parents=True, exist_ok=True)
    eng.logger = MagicMock()
    eng.quality_manager = MagicMock()
    eng.token_manager = MagicMock()
    eng.generate_note = MagicMock()
    eng.generate_batch = MagicMock()
    eng.generate_synthesis = MagicMock()
    eng.generate_synthesis_two_stage = MagicMock()
    eng.update_synthesis_incremental = MagicMock()
    eng.flush_pending_synthesis = MagicMock()
    eng.print_batch_summary = MagicMock()
    eng.check_only = MagicMock()
    return eng


def _make_args(**overrides):
    """Create a MagicMock args object with sensible defaults."""
    defaults = dict(
        check_only='',
        search='',
        tags=None,
        youtube='',
        youtube_playlist='',
        title='',
        provider=None,
        force=False,
        mode='lecture',
        with_context=False,
        context_limit=3,
        bilibili=[],
        audio_url='',
        input=[],
        podcast_subscribe='',
        podcast_name='',
        podcast_unsubscribe='',
        podcast_process='',
        podcast_max=5,
        podcast_sync='',
        skip_existing=False,
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


# ============================================================
# Test: _show_cached_quality
# ============================================================

class TestShowCachedQuality:
    """_show_cached_quality 函数测试"""

    def test_no_note_path_returns_early(self):
        """note_path 为空时直接返回，不调用 check_only"""
        from noteforge.cli.commands import _show_cached_quality
        engine = _make_engine(Path.cwd() / "tmp_test")
        with patch('os.path.exists', return_value=False):
            _show_cached_quality(engine, '')
        engine.check_only.assert_not_called()

    def test_note_path_not_on_disk_returns_early(self):
        """note_path 不存在时直接返回"""
        from noteforge.cli.commands import _show_cached_quality
        engine = _make_engine(Path.cwd() / "tmp_test")
        with patch('os.path.exists', return_value=False):
            _show_cached_quality(engine, '/nonexistent/path.md')
        engine.check_only.assert_not_called()

    def test_check_only_exception_silently_caught(self):
        """check_only 抛异常时不崩溃"""
        from noteforge.cli.commands import _show_cached_quality
        engine = _make_engine(Path.cwd() / "tmp_test")
        engine.check_only.side_effect = Exception("boom")
        with patch('os.path.exists', return_value=True):
            _show_cached_quality(engine, '/some/note.md')

    def test_report_with_score_prints_quality_line(self, capsys):
        """check_only 返回报告时打印质量摘要"""
        from noteforge.cli.commands import _show_cached_quality
        engine = _make_engine(Path.cwd() / "tmp_test")
        report = {'total_score': 0.85, 'overall_passed': True}
        engine.check_only.return_value = report
        with patch('os.path.exists', return_value=True):
            _show_cached_quality(engine, '/some/note.md')
        out = capsys.readouterr().out
        assert '质量' in out
        assert '85%' in out


# ============================================================
# Test: run_check_only
# ============================================================

class TestRunCheckOnly:
    """run_check_only 函数测试"""

    def test_file_does_not_exist_returns_1(self, capsys):
        """文件不存在时打印错误并返回 1"""
        from noteforge.cli.commands import run_check_only
        engine = _make_engine(Path.cwd() / "tmp_test")
        args = _make_args(check_only='/nonexistent.md')
        with patch('os.path.exists', return_value=False):
            ret = run_check_only(engine, args)
        assert ret == 1
        assert 'ERROR' in capsys.readouterr().out

    def test_check_only_returns_none_returns_1(self, capsys):
        """check_only 返回 None 时返回 1"""
        from noteforge.cli.commands import run_check_only
        engine = _make_engine(Path.cwd() / "tmp_test")
        engine.check_only.return_value = None
        args = _make_args(check_only='/some.md')
        with patch('os.path.exists', return_value=True):
            ret = run_check_only(engine, args)
        assert ret == 1

    def test_report_passed_returns_0(self):
        """报告通过时返回 0"""
        from noteforge.cli.commands import run_check_only
        engine = _make_engine(Path.cwd() / "tmp_test")
        engine.check_only.return_value = {'total_score': 0.9, 'overall_passed': True}
        args = _make_args(check_only='/some.md')
        with patch('os.path.exists', return_value=True):
            ret = run_check_only(engine, args)
        assert ret == 0

    def test_report_failed_returns_1(self):
        """报告未通过时返回 1"""
        from noteforge.cli.commands import run_check_only
        engine = _make_engine(Path.cwd() / "tmp_test")
        engine.check_only.return_value = {'total_score': 0.3, 'overall_passed': False}
        args = _make_args(check_only='/some.md')
        with patch('os.path.exists', return_value=True):
            ret = run_check_only(engine, args)
        assert ret == 1


# ============================================================
# Test: run_search
# ============================================================

class TestRunSearch:
    """run_search 函数测试"""

    def test_no_results_prints_not_found(self, capsys):
        """无结果时打印未找到并返回 0"""
        from noteforge.cli.commands import run_search
        engine = _make_engine(Path.cwd() / "tmp_test")
        args = _make_args(search='nonexistent_query')
        mock_idx = MagicMock()
        mock_idx.search.return_value = []
        _patch_knowledge_index_and_run(run_search, engine, args, mock_idx)
        out = capsys.readouterr().out
        assert '未找到' in out

    def test_results_found_prints_summary(self, capsys):
        """有结果时打印结果列表并返回 0"""
        from noteforge.cli.commands import run_search
        engine = _make_engine(Path.cwd() / "tmp_test")
        fake_results = [
            MagicMock(date='2025-06-01', title='Test Note', relevance=0.9,
                      tags=['tag1', 'tag2'], snippet='some snippet text here')
        ]
        args = _make_args(search='test', tags=None)
        mock_idx = MagicMock()
        mock_idx.search.return_value = fake_results
        _patch_knowledge_index_and_run(run_search, engine, args, mock_idx)
        out = capsys.readouterr().out
        assert 'Test Note' in out
        assert '1.' in out


def _patch_knowledge_index_and_run(func, engine, args, mock_idx):
    """Helper: patch KnowledgeIndex in sys.modules, reload commands, run func."""
    with patch.dict(sys.modules, {
        'noteforge.intelligence.knowledge_index': MagicMock(
            KnowledgeIndex=MagicMock(return_value=mock_idx)
        )
    }):
        import importlib
        from noteforge.cli import commands as cmds
        importlib.reload(cmds)
        cmds.run_search(engine, args)


# ============================================================
# Test: run_list_notes
# ============================================================

class TestRunListNotes:
    """run_list_notes 函数测试"""

    def test_empty_index_prints_empty_message(self, capsys):
        """笔记库为空时打印提示并返回 0"""
        from noteforge.cli.commands import run_list_notes
        engine = _make_engine(Path.cwd() / "tmp_test")
        args = _make_args()
        mock_idx = MagicMock()
        mock_idx.list_notes.return_value = []
        mock_idx.get_all_tags.return_value = {}
        _patch_list_notes_and_run(run_list_notes, engine, args, mock_idx)
        out = capsys.readouterr().out
        assert '笔记库为空' in out

    def test_with_notes_prints_summary(self, capsys):
        """有笔记时打印概览并返回 0"""
        from noteforge.cli.commands import run_list_notes
        engine = _make_engine(Path.cwd() / "tmp_test")
        fake_note = MagicMock(
            date='2025-06-01', title='Episode 1', char_count=5000,
            key_frameworks=['model_a'], action_items=['do_x'], tags=['tag1']
        )
        args = _make_args()
        mock_idx = MagicMock()
        mock_idx.list_notes.return_value = [fake_note]
        mock_idx.get_all_tags.return_value = {'tag1': 5}
        _patch_list_notes_and_run(run_list_notes, engine, args, mock_idx)
        out = capsys.readouterr().out
        assert 'Episode 1' in out
        assert '1 篇' in out


def _patch_list_notes_and_run(func, engine, args, mock_idx):
    """Helper: patch KnowledgeIndex, reload, run run_list_notes."""
    with patch.dict(sys.modules, {
        'noteforge.intelligence.knowledge_index': MagicMock(
            KnowledgeIndex=MagicMock(return_value=mock_idx)
        )
    }):
        import importlib
        from noteforge.cli import commands as cmds
        importlib.reload(cmds)
        cmds.run_list_notes(engine, args)


# ============================================================
# Test: run_youtube
# ============================================================

class TestRunYouTube:
    """run_youtube 函数测试"""

    def test_successful_download_and_generation_returns_0(self):
        """下载成功 + 生成成功 → 返回 0"""
        from noteforge.cli.commands import run_youtube
        engine = _make_engine(Path.cwd() / "tmp_test")
        result = FakeResult(error=None)
        engine.generate_note.return_value = result
        args = _make_args(
            youtube='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            title='Test Video',
            provider=None, force=False, mode='lecture',
            with_context=False, context_limit=3,
        )
        mock_yt = MagicMock()
        mock_yt.download_audio.return_value = {
            'path': str(engine.base_dir / 'output' / 'audio' / 'test.mp3'),
            'title': 'Test Video',
        }
        with patch('noteforge.sources.youtube.YouTubeHandler', return_value=mock_yt):
            ret = run_youtube(engine, args)
        assert ret == 0
        engine.flush_pending_synthesis.assert_called_once()

    def test_download_raises_returns_1(self, capsys):
        """下载过程抛异常 → 返回 1"""
        from noteforge.cli.commands import run_youtube
        engine = _make_engine(Path.cwd() / "tmp_test")
        args = _make_args(youtube='https://www.youtube.com/watch?v=abc123')
        with patch('noteforge.sources.youtube.YouTubeHandler',
                   side_effect=Exception('network error')):
            ret = run_youtube(engine, args)
        assert ret == 1
        out = capsys.readouterr().out
        assert 'ERROR' in out

    def test_generation_error_already_exists_shows_cached_quality(self):
        """生成返回含 "已存在" 错误 → 调用 _show_cached_quality，返回 0"""
        from noteforge.cli.commands import run_youtube
        engine = _make_engine(Path.cwd() / "tmp_test")
        result = FakeResult(error='已存在（使用 --force 覆盖）', note_path='/notes/ep01.md')
        engine.generate_note.return_value = result
        args = _make_args(youtube='https://www.youtube.com/watch?v=abc123')
        mock_yt = MagicMock()
        mock_yt.download_audio.return_value = {'path': '/tmp/a.mp3', 'title': 'V'}
        with patch('noteforge.sources.youtube.YouTubeHandler', return_value=mock_yt):
            with patch('noteforge.cli.commands.sources._show_cached_quality') as mock_show:
                ret = run_youtube(engine, args)
        assert ret == 0
        mock_show.assert_called_once_with(engine, '/notes/ep01.md')

    def test_generation_other_error_returns_1(self, capsys):
        """生成返回其他错误 → 返回 1"""
        from noteforge.cli.commands import run_youtube
        engine = _make_engine(Path.cwd() / "tmp_test")
        result = FakeResult(error='LLM timeout')
        engine.generate_note.return_value = result
        args = _make_args(youtube='https://www.youtube.com/watch?v=abc123')
        mock_yt = MagicMock()
        mock_yt.download_audio.return_value = {'path': '/tmp/a.mp3', 'title': 'V'}
        with patch('noteforge.sources.youtube.YouTubeHandler', return_value=mock_yt):
            ret = run_youtube(engine, args)
        assert ret == 1


# ============================================================
# Test: run_youtube_playlist
# ============================================================

class TestRunYouTubePlaylist:
    """run_youtube_playlist 函数测试"""

    def test_all_downloads_succeed_returns_0(self):
        """所有视频下载+生成成功 → 返回 0"""
        from noteforge.cli.commands import run_youtube_playlist
        engine = _make_engine(Path.cwd() / "tmp_test")
        engine.generate_note.return_value = FakeResult(error=None)
        args = _make_args(
            youtube_playlist='https://www.youtube.com/playlist?list=abc',
            provider=None, force=False, mode='lecture',
            with_context=False, context_limit=3,
        )
        results_list = [
            {'path': '/tmp/v1.mp3', 'title': 'Video 1'},
            {'path': '/tmp/v2.mp3', 'title': 'Video 2'},
        ]
        mock_yt = MagicMock()
        mock_yt.download_playlist.return_value = results_list
        with patch('noteforge.sources.youtube.YouTubeHandler', return_value=mock_yt):
            ret = run_youtube_playlist(engine, args)
        assert ret == 0
        assert engine.generate_note.call_count == 2

    def test_error_during_processing_returns_1(self, capsys):
        """处理过程中抛异常 → 返回 1"""
        from noteforge.cli.commands import run_youtube_playlist
        engine = _make_engine(Path.cwd() / "tmp_test")
        args = _make_args(youtube_playlist='https://www.youtube.com/playlist?list=abc')
        with patch('noteforge.sources.youtube.YouTubeHandler',
                   side_effect=Exception('fail')):
            ret = run_youtube_playlist(engine, args)
        assert ret == 1
        out = capsys.readouterr().out
        assert 'ERROR' in out


# ============================================================
# Test: run_bilibili
# ============================================================

class TestRunBilibili:
    """run_bilibili 函数测试"""

    def test_single_url_success_returns_0(self):
        """单个 URL 下载+生成成功 → 返回 0"""
        from noteforge.cli.commands import run_bilibili
        engine = _make_engine(Path.cwd() / "tmp_test")
        result = FakeResult(error=None)
        engine.generate_note.return_value = result
        args = _make_args(
            bilibili='https://www.bilibili.com/video/BV1xx411c7mD',
            title='', provider=None, force=False, mode='lecture',
            with_context=False, context_limit=3,
        )
        meta = {'success': True, 'path': '/tmp/bv.mp3', 'title': 'BV Title',
                'method': 'yt-dlp'}
        with patch('noteforge.sources.bilibili.download_bilibili',
                   return_value=meta):
            ret = run_bilibili(engine, args)
        assert ret == 0

    def test_download_failure_increments_errors_returns_1(self, capsys):
        """下载失败 → errors 增加，最终返回 1"""
        from noteforge.cli.commands import run_bilibili
        engine = _make_engine(Path.cwd() / "tmp_test")
        args = _make_args(bilibili='https://www.bilibili.com/video/BV1xx411c7mD')
        meta = {'success': False, 'error': 'Download failed'}
        with patch('noteforge.sources.bilibili.download_bilibili',
                   return_value=meta):
            ret = run_bilibili(engine, args)
        assert ret == 1
        engine.logger.error.assert_called()

    def test_generation_other_error_returns_1(self, capsys):
        """生成返回非 "已存在" 错误 → 打印错误，返回 1"""
        from noteforge.cli.commands import run_bilibili
        engine = _make_engine(Path.cwd() / "tmp_test")
        result = FakeResult(error='LLM timeout')
        engine.generate_note.return_value = result
        args = _make_args(
            bilibili='https://www.bilibili.com/video/BV1xx411c7mD',
        )
        meta = {'success': True, 'path': '/tmp/bv.mp3', 'title': 'BV Title',
                'method': 'yt-dlp'}
        with patch('noteforge.sources.bilibili.download_bilibili',
                   return_value=meta):
            ret = run_bilibili(engine, args)
        assert ret == 1

    def test_exception_during_processing_increments_errors(self, capsys):
        """处理过程中抛异常 → errors 增加，返回 1"""
        from noteforge.cli.commands import run_bilibili
        engine = _make_engine(Path.cwd() / "tmp_test")
        args = _make_args(bilibili='https://www.bilibili.com/video/BV1xx411c7mD')
        with patch('noteforge.sources.bilibili.download_bilibili',
                   side_effect=Exception('boom')):
            ret = run_bilibili(engine, args)
        assert ret == 1
        engine.logger.error.assert_called()


# ============================================================
# Test: run_audio_url
# ============================================================

class TestRunAudioUrl:
    """run_audio_url 函数测试"""

    def test_yt_dlp_succeeds_generates_note(self, tmp_path):
        """yt-dlp 成功 → 调用 generate_note → 返回 0"""
        from noteforge.cli.commands import run_audio_url
        engine = _make_engine(tmp_path)
        audio_dir = tmp_path / 'output' / 'audio'
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_file = str(audio_dir / 'audio.mp3')

        class Result:
            error = None
            note_path = audio_file

        engine.generate_note.return_value = Result()
        args = _make_args(audio_url='https://example.com/podcast/ep01', title='')

        fake_dl = MagicMock()
        fake_dl.MediaDownloader.try_ytdlp.return_value = audio_file

        def fake_exists(p):
            return p == audio_file

        with patch.dict(sys.modules, {'noteforge.sources.downloader': fake_dl}):
            with patch('os.path.exists', side_effect=fake_exists):
                with patch('os.path.basename', return_value='audio.mp3'):
                    with patch('os.path.splitext', return_value=('audio', '.mp3')):
                        ret = run_audio_url(engine, args)
        assert ret == 0
        engine.generate_note.assert_called_once()

    def test_all_strategies_fail_returns_1(self, capsys):
        """所有下载策略均失败 → 返回 1"""
        from noteforge.cli.commands import run_audio_url
        engine = _make_engine(Path.cwd() / "tmp_test")
        args = _make_args(audio_url='https://example.com/audio')

        mock_downloader = MagicMock()
        mock_downloader.try_ytdlp.return_value = None

        with patch('noteforge.sources.downloader.MediaDownloader',
                   return_value=mock_downloader):
            with patch('os.path.exists', return_value=False):
                ret = run_audio_url(engine, args)
        assert ret == 1
        out = capsys.readouterr().out
        assert '下载策略均失败' in out


# ============================================================
# Test: run_synthesis
# ============================================================

class TestRunSynthesis:
    """run_synthesis 函数测试"""

    def test_valid_file_paths_calls_generate_synthesis(self):
        """提供有效路径 → 调用 engine.generate_synthesis"""
        from noteforge.cli.commands import run_synthesis
        engine = _make_engine(Path.cwd() / "tmp_test")
        engine.generate_synthesis.return_value = '/output/synthesis.md'
        args = _make_args(input=['/notes/ep01.md'], provider=None)
        with patch('os.path.exists', return_value=True):
            ret = run_synthesis(engine, args)
        assert ret == 0
        engine.generate_synthesis.assert_called_once()

    def test_result_is_none_returns_1(self, capsys):
        """生成失败（返回 None）→ 返回 1"""
        from noteforge.cli.commands import run_synthesis
        engine = _make_engine(Path.cwd() / "tmp_test")
        engine.generate_synthesis.return_value = None
        args = _make_args(input=['/notes/ep01.md'], provider=None)
        with patch('os.path.exists', return_value=True):
            ret = run_synthesis(engine, args)
        assert ret == 1
        out = capsys.readouterr().out
        assert '合成失败' in out


# ============================================================
# Test: run_synthesis_2stage
# ============================================================

class TestRunSynthesis2Stage:
    """run_synthesis_2stage 函数测试"""

    def test_with_input_calls_two_stage_engine(self):
        """提供 input → 调用 engine.generate_synthesis_two_stage"""
        from noteforge.cli.commands import run_synthesis_2stage
        engine = _make_engine(Path.cwd() / "tmp_test")
        engine.generate_synthesis_two_stage.return_value = '/output/2stage.md'
        args = _make_args(input=['/notes/ep01.md'], provider=None, domain=None)
        with patch('os.path.exists', return_value=True):
            ret = run_synthesis_2stage(engine, args)
        assert ret == 0
        engine.generate_synthesis_two_stage.assert_called_once()
        engine.token_manager.print_summary.assert_called_once()


# ============================================================
# Test: run_synthesis_incremental
# ============================================================

class TestRunSynthesisIncremental:
    """run_synthesis_incremental 函数测试"""

    def test_no_input_returns_1(self, capsys):
        """未指定 input → 返回 1"""
        from noteforge.cli.commands import run_synthesis_incremental
        engine = _make_engine(Path.cwd() / "tmp_test")
        args = _make_args(input=[], provider=None)
        ret = run_synthesis_incremental(engine, args)
        assert ret == 1
        out = capsys.readouterr().out
        assert 'ERROR' in out

    def test_valid_input_calls_update_synthesis(self):
        """提供有效 input → 调用 engine.update_synthesis_incremental"""
        from noteforge.cli.commands import run_synthesis_incremental
        engine = _make_engine(Path.cwd() / "tmp_test")
        engine.update_synthesis_incremental.return_value = '/output/inc.md'
        args = _make_args(input=['/notes/ep01.md'], provider=None)
        with patch('os.path.exists', return_value=True):
            ret = run_synthesis_incremental(engine, args)
        assert ret == 0
        engine.update_synthesis_incremental.assert_called_once_with(
            new_note_path='/notes/ep01.md', provider_override=None
        )


# ============================================================
# Test: run_podcast_subscribe
# ============================================================

class TestRunPodcastSubscribe:
    """run_podcast_subscribe 函数测试"""

    def test_success_returns_0(self, capsys):
        """订阅成功 → 返回 0"""
        from noteforge.cli.commands import run_podcast_subscribe
        engine = _make_engine(Path.cwd() / "tmp_test")
        info = {'name': 'My Podcast', 'feed_url': 'https://example.com/feed',
                'episode_count': 42}
        args = _make_args(podcast_subscribe='https://example.com/feed',
                          podcast_name='My Podcast')
        mock_ph = MagicMock()
        mock_ph.subscribe.return_value = info
        with patch('noteforge.sources.podcast.PodcastHandler',
                   return_value=mock_ph):
            ret = run_podcast_subscribe(engine, args)
        assert ret == 0
        out = capsys.readouterr().out
        assert '已订阅' in out
        assert 'My Podcast' in out

    def test_subscribe_exception_returns_1(self, capsys):
        """subscribe 方法抛异常 → 返回 1"""
        from noteforge.cli.commands import run_podcast_subscribe
        engine = _make_engine(Path.cwd() / "tmp_test")
        args = _make_args(podcast_subscribe='https://bad-url', podcast_name='')
        mock_ph = MagicMock()
        mock_ph.subscribe.side_effect = Exception('bad feed')
        with patch('noteforge.sources.podcast.PodcastHandler',
                   return_value=mock_ph):
            ret = run_podcast_subscribe(engine, args)
        assert ret == 1
        out = capsys.readouterr().out
        assert 'ERROR' in out


# ============================================================
# Test: run_batch
# ============================================================

class TestRunBatch:
    """run_batch 函数测试"""

    def test_no_failures_returns_0(self):
        """全部成功 → 返回 0"""
        from noteforge.cli.commands import run_batch
        engine = _make_engine(Path.cwd() / "tmp_test")
        engine.generate_batch.return_value = [
            FakeResult(error=None),
            FakeResult(error=None),
        ]
        args = _make_args(
            skip_existing=False, title=None,
            provider=None, force=False, mode='lecture',
            with_context=False, context_limit=3,
        )
        ret = run_batch(engine, args)
        assert ret == 0

    def test_with_failures_returns_1(self):
        """有失败项 → 返回 1"""
        from noteforge.cli.commands import run_batch
        engine = _make_engine(Path.cwd() / "tmp_test")
        engine.generate_batch.return_value = [
            FakeResult(error=None),
            FakeResult(error='LLM failed'),
        ]
        args = _make_args(
            skip_existing=False, title=None,
            provider=None, force=False, mode='lecture',
            with_context=False, context_limit=3,
        )
        ret = run_batch(engine, args)
        assert ret == 1


# ============================================================
# Test: run_single_note
# ============================================================

class TestRunSingleNote:
    """run_single_note 函数测试"""

    def test_valid_file_path_generates_note(self):
        """有效文件路径 → 调用 generate_note"""
        from noteforge.cli.commands import run_single_note
        engine = _make_engine(Path.cwd() / "tmp_test")
        result = FakeResult(error=None, total_score=0, overall_passed=False)
        engine.generate_note.return_value = result
        args = _make_args(
            input=['/transcripts/ep01.txt'],
            provider=None, force=False, mode='lecture',
            with_context=False, context_limit=3, title=None,
        )
        with patch('os.path.exists', return_value=True):
            ret = run_single_note(engine, args)
        assert ret == 0
        engine.generate_note.assert_called_once()

    def test_no_valid_inputs_returns_1(self, capsys):
        """无有效输入 → 返回 1"""
        from noteforge.cli.commands import run_single_note
        engine = _make_engine(Path.cwd() / "tmp_test")
        args = _make_args(
            input=['nonexistent'],
            provider=None, force=False, mode='lecture',
            with_context=False, context_limit=3, title=None,
        )
        with patch('os.path.exists', return_value=False):
            ret = run_single_note(engine, args)
        assert ret == 1
        out = capsys.readouterr().out
        assert 'ERROR' in out

    def test_multiple_files_calls_generate_batch(self):
        """多个文件 → 调用 generate_batch"""
        from noteforge.cli.commands import run_single_note
        engine = _make_engine(Path.cwd() / "tmp_test")
        engine.generate_batch.return_value = [
            FakeResult(error='已存在（跳过）'),
            FakeResult(error=None),
        ]
        args = _make_args(
            input=['/transcripts/ep01.txt', '/transcripts/ep02.txt'],
            provider=None, force=False, mode='lecture',
            with_context=False, context_limit=3, title=None,
        )
        with patch('os.path.exists', return_value=True):
            ret = run_single_note(engine, args)
        assert ret == 0
        engine.generate_batch.assert_called_once()
        call_kwargs = engine.generate_batch.call_args[1]
        assert 'transcript_paths' in call_kwargs
