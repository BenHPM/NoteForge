# -*- coding: utf-8 -*-
"""
LocalSource 本地文件数据源单元测试

覆盖 noteforge/sources/local.py 的 LocalSource。

运行：
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_local_source.py -v
"""
import os
import wave
import contextlib
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ['NOTEFORGE_SKIP_ENV_CHECK'] = '1'

import pytest
from noteforge.sources.local import LocalSource, AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, ALL_EXTENSIONS


@pytest.fixture
def local_source():
    return LocalSource()


class TestCanHandle:
    """can_handle 方法测试"""

    def test_existing_audio_file_returns_true(self, local_source, tmp_path):
        """存在的音频文件应返回 True"""
        audio_file = tmp_path / "test.mp3"
        audio_file.touch()
        assert local_source.can_handle(str(audio_file)) is True

    def test_existing_video_file_returns_true(self, local_source, tmp_path):
        """存在的视频文件应返回 True"""
        video_file = tmp_path / "test.mp4"
        video_file.touch()
        assert local_source.can_handle(str(video_file)) is True

    def test_nonexistent_file_returns_false(self, local_source, tmp_path):
        """不存在的文件应返回 False"""
        assert local_source.can_handle(str(tmp_path / "nonexistent.mp3")) is False

    def test_unsupported_extension_returns_false(self, local_source, tmp_path):
        """不支持的扩展名应返回 False"""
        f = tmp_path / "test.txt"
        f.touch()
        assert local_source.can_handle(str(f)) is False

    def test_directory_returns_false(self, local_source, tmp_path):
        """目录应返回 False"""
        assert local_source.can_handle(str(tmp_path)) is False

    def test_empty_string_returns_false(self, local_source):
        """空字符串应返回 False"""
        assert local_source.can_handle("") is False

    def test_all_audio_extensions(self, local_source, tmp_path):
        """所有音频扩展名都应被识别"""
        for ext in AUDIO_EXTENSIONS:
            f = tmp_path / f"test{ext}"
            f.touch()
            assert local_source.can_handle(str(f)) is True, f"音频扩展名 {ext} 应被支持"

    def test_all_video_extensions(self, local_source, tmp_path):
        """所有视频扩展名都应被识别"""
        for ext in VIDEO_EXTENSIONS:
            f = tmp_path / f"test{ext}"
            f.touch()
            assert local_source.can_handle(str(f)) is True, f"视频扩展名 {ext} 应被支持"

    def test_case_insensitive_extension(self, local_source, tmp_path):
        """扩展名大小写不敏感"""
        f = tmp_path / "test.MP3"
        f.touch()
        assert local_source.can_handle(str(f)) is True


class TestFetchAudio:
    """fetch 音频文件测试"""

    def test_fetch_audio_same_dir_no_copy(self, local_source, tmp_path):
        """音频文件已在输出目录时，路径不变"""
        audio_file = tmp_path / "podcast.mp3"
        audio_file.write_bytes(b"fake mp3 content")

        result = local_source.fetch(str(audio_file), str(tmp_path))
        assert result.error is None
        assert result.audio_path == str(audio_file)
        assert result.title == "podcast"
        assert result.source_type == "local"

    def test_fetch_audio_copies_to_output_dir(self, local_source, tmp_path):
        """音频文件不在输出目录时复制到目标目录"""
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        dst_dir = tmp_path / "output"
        dst_dir.mkdir()

        audio_file = src_dir / "interview.wav"
        audio_file.write_bytes(b"fake wav content")

        result = local_source.fetch(str(audio_file), str(dst_dir))
        assert result.error is None
        assert result.audio_path == str(dst_dir / "interview.wav")
        assert (dst_dir / "interview.wav").exists()
        # 原文件应保留
        assert audio_file.exists()

    def test_fetch_audio_wav_extension(self, local_source, tmp_path):
        """支持 .wav 文件"""
        f = tmp_path / "audio.wav"
        f.write_bytes(b"fake wav")

        result = local_source.fetch(str(f), str(tmp_path))
        assert result.error is None
        assert result.audio_path == str(f)

    def test_fetch_audio_m4a_extension(self, local_source, tmp_path):
        """支持 .m4a 文件"""
        f = tmp_path / "audio.m4a"
        f.write_bytes(b"fake m4a")

        result = local_source.fetch(str(f), str(tmp_path))
        assert result.error is None
        assert result.audio_path == str(f)

    def test_fetch_audio_flac_extension(self, local_source, tmp_path):
        """支持 .flac 文件"""
        f = tmp_path / "audio.flac"
        f.write_bytes(b"fake flac")

        result = local_source.fetch(str(f), str(tmp_path))
        assert result.error is None
        assert result.audio_path == str(f)

    def test_fetch_title_is_stem(self, local_source, tmp_path):
        """标题应为文件名（不含扩展名）"""
        f = tmp_path / "我的播客第3集.mp3"
        f.write_bytes(b"fake mp3")

        result = local_source.fetch(str(f), str(tmp_path))
        assert result.title == "我的播客第3集"


class TestFetchVideoWithMockedFfmpeg:
    """使用 mock ffmpeg 的视频文件测试"""

    def test_video_extract_output_path_and_title(self, tmp_path):
        """验证视频提取的目标路径和标题"""
        # Create a minimal valid WAV file (44 bytes of RIFF header)
        wav_path = tmp_path / "input.wav"
        with contextlib.closing(wave.open(str(wav_path), 'w')) as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b'\x00\x00' * 100)

        # Rename to .mp4 to trigger video path
        video_file = tmp_path / "talk.mp4"
        wav_path.rename(video_file)

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            # Simulate ffmpeg creating output
            expected_output = str(output_dir / "talk.wav")
            with contextlib.closing(wave.open(expected_output, 'w')) as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b'\x00\x00' * 100)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        source = LocalSource()
        with patch('noteforge.sources.local.subprocess.run', side_effect=mock_run):
            result = source.fetch(str(video_file), str(output_dir))

        assert result.error is None
        assert result.audio_path == str(output_dir / "talk.wav")
        assert result.title == "talk"
        assert result.source_type == "local"
        assert len(calls) == 1
        assert 'ffmpeg' in calls[0][0]

    def test_video_ffmpeg_failure_returns_error(self, tmp_path):
        """ffmpeg 失败时应返回错误"""
        source = LocalSource()

        video_file = tmp_path / "corrupt.mp4"
        video_file.write_bytes(b"not a video")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Invalid data"

        with patch('noteforge.sources.local.subprocess.run', return_value=mock_result):
            result = source.fetch(str(video_file), str(output_dir))

        assert result.error is not None
        assert "ffmpeg" in result.error.lower()
        assert result.audio_path == ""
        assert result.source_type == "local"


class TestFetchErrors:
    """错误处理测试"""

    def test_fetch_nonexistent_file_returns_error(self, local_source, tmp_path):
        """文件不存在时返回错误"""
        result = local_source.fetch(str(tmp_path / "ghost.mp3"))
        assert result.error is not None
        assert "不存在" in result.error
        assert result.audio_path == ""
        assert result.source_type == "local"


class TestFetchWavCreated:
    """使用真实 WAV 文件的集成测试"""

    def test_fetch_valid_wav_is_accepted(self, tmp_path):
        """创建一个真实 WAV 文件并验证被接受为音频"""
        wav_path = tmp_path / "recording.wav"
        with contextlib.closing(wave.open(str(wav_path), 'w')) as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b'\x00\x00' * 1600)  # 0.1s of silence

        source = LocalSource()
        assert source.can_handle(str(wav_path)) is True

        result = source.fetch(str(wav_path), str(tmp_path))
        assert result.error is None
        assert result.audio_path == str(wav_path)
        assert result.title == "recording"
        assert result.source_type == "local"


class TestConstants:
    """常量集合完整性测试"""

    def test_audio_extensions_non_empty(self):
        """音频扩展名列表不应为空"""
        assert len(AUDIO_EXTENSIONS) > 0

    def test_video_extensions_non_empty(self):
        """视频扩展名列表不应为空"""
        assert len(VIDEO_EXTENSIONS) > 0

    def test_no_overlap_audio_video(self):
        """音频和视频扩展名不应重叠"""
        assert AUDIO_EXTENSIONS.isdisjoint(VIDEO_EXTENSIONS)

    def test_all_extensions_union(self):
        """ALL_EXTENSIONS 应为两者的并集"""
        assert ALL_EXTENSIONS == AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
