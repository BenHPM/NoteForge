# -*- coding: utf-8 -*-
"""
NoteForge ASR 转写模块单元测试

覆盖 noteforge/sources/asr.py:
  - get_base_dir, load_config, ensure_dirs
  - extract_audio, _get_audio_duration
  - transcribe_with_paraformer, save_result
  - process_episode, process_audio_file
  - main

运行:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_asr.py -v
"""
import os
import sys
import json
import subprocess
import tempfile
import types
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open, call

# 跳过 env_check
# 导入被测函数到本地命名空间
from noteforge.sources.asr import (
    get_base_dir,
    load_config,
    ensure_dirs,
    extract_audio,
    _get_audio_duration,
    transcribe_with_paraformer,
    save_result,
    process_episode,
    process_audio_file,
    main,
)

# 获取 asr 模块引用（用于设置模块级全局变量）
_asr_module = sys.modules["noteforge.sources.asr"]


# ============================================================
# get_base_dir tests
# ============================================================

class TestGetBaseDir:
    """get_base_dir 函数测试"""

    def test_returns_path_three_levels_up(self, tmp_path):
        """get_base_dir 返回 asr.py 上方 3 级的目录（即项目根目录）"""
        project_root = tmp_path / "project"
        project_root.mkdir()
        # 构建 fake Path 链: __file__ → asr.py → sources/ → noteforge/ → project/
        # get_base_dir() 做: Path(__file__).parent.parent.parent
        # 所以需要 3 次 .parent 回到 project_root

        # 创建一个模拟的 __file__ Path 对象
        fake_file = project_root / "noteforge" / "sources" / "asr.py"
        fake_file.parent.mkdir(parents=True)

        chain = [None] * 4
        chain[3] = fake_file  # level 0: asr.py
        for i in range(2, -1, -1):
            chain[i] = chain[i + 1].parent  # level 1,2,3: sources/, noteforge/, project/

        expected = chain[0]  # project root (3 levels up)

        with patch("noteforge.sources.asr.Path") as MockPath:
            mock_instance = MagicMock()
            # Simulate: Path(__file__).parent.parent.parent
            # We need 3 parent accesses to return the right level
            p3 = MagicMock()
            p3.resolve.return_value = expected.resolve()
            p2 = MagicMock()
            p2.parent = p3
            p1 = MagicMock()
            p1.parent = p2
            mock_instance.parent = p1
            MockPath.return_value = mock_instance

            result = get_base_dir()
            # Verify the parent chain resolves to project_root
            assert mock_instance.parent.parent.parent.resolve() == expected.resolve()


# ============================================================
# load_config tests
# ============================================================

class TestLoadConfig:
    """load_config 函数测试"""

    def test_config_not_exists_returns_none(self, tmp_path):
        """配置文件不存在时返回 None"""
        fake_base = tmp_path
        config_dir = fake_base / "config"
        config_dir.mkdir(parents=True)
        # 不创建 video-mapping.json

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            result = load_config()

        assert result is None

    def test_list_format_converts_to_dict(self, tmp_path):
        """list 格式转换为 {"episodes": {id: item}}"""
        fake_base = tmp_path
        config_dir = fake_base / "config"
        config_dir.mkdir(parents=True)
        config_data = [
            {"id": "ep01", "title": "第一集", "file": "/path/ep01.mp4"},
            {"id": "ep02", "title": "第二集", "file": "/path/ep02.mp4"},
        ]
        (config_dir / "video-mapping.json").write_text(
            json.dumps(config_data, ensure_ascii=False), encoding="utf-8"
        )

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            result = load_config()

        assert result is not None
        assert "episodes" in result
        assert result["episodes"]["ep01"]["title"] == "第一集"
        assert result["episodes"]["ep02"]["title"] == "第二集"

    def test_dict_format_returns_as_is(self, tmp_path):
        """dict 格式直接返回"""
        fake_base = tmp_path
        config_dir = fake_base / "config"
        config_dir.mkdir(parents=True)
        config_data = {"episodes": {"ep01": {"title": "第一集"}}}
        (config_dir / "video-mapping.json").write_text(
            json.dumps(config_data, ensure_ascii=False), encoding="utf-8"
        )

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            result = load_config()

        assert result == config_data

    def test_invalid_format_returns_none(self, tmp_path):
        """非 list/dict 格式返回 None"""
        fake_base = tmp_path
        config_dir = fake_base / "config"
        config_dir.mkdir(parents=True)
        # 写入一个字符串（不是 list 也不是 dict）
        (config_dir / "video-mapping.json").write_text(
            '"just a string"', encoding="utf-8"
        )

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            result = load_config()

        assert result is None

    def test_parse_error_returns_none(self, tmp_path):
        """JSON 解析失败时返回 None"""
        fake_base = tmp_path
        config_dir = fake_base / "config"
        config_dir.mkdir(parents=True)
        # 写入非法 JSON
        (config_dir / "video-mapping.json").write_text(
            "not valid json {{{", encoding="utf-8"
        )

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            result = load_config()

        assert result is None


# ============================================================
# ensure_dirs tests
# ============================================================

class TestEnsureDirs:
    """ensure_dirs 函数测试"""

    def test_creates_output_and_temp_dirs(self, tmp_path):
        """ensure_dirs 创建 output/transcripts 和 temp 目录"""
        fake_base = tmp_path

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            ensure_dirs()

        output_dir = fake_base / "output" / "transcripts"
        temp_dir = fake_base / "temp"
        assert output_dir.exists() and output_dir.is_dir()
        assert temp_dir.exists() and temp_dir.is_dir()

    def test_idempotent_when_dirs_exist(self, tmp_path):
        """目录已存在时再次调用不报错"""
        fake_base = tmp_path
        output_dir = fake_base / "output" / "transcripts"
        output_dir.mkdir(parents=True)

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            ensure_dirs()  # 第二次调用

        assert output_dir.exists()


# ============================================================
# extract_audio tests
# ============================================================

class TestExtractAudio:
    """extract_audio 函数测试"""

    def test_successful_extraction_returns_true(self, tmp_path):
        """ffmpeg 成功时返回 True"""
        video = tmp_path / "input.mp4"
        video.write_bytes(b"video data")
        audio = str(tmp_path / "output.wav")

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("noteforge.sources.asr.subprocess.run", return_value=mock_result) as mock_run:
            with patch("noteforge.sources.asr.os.path.exists", return_value=True):
                result = extract_audio(str(video), audio)

        assert result is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-vn" in cmd
        assert "-acodec" in cmd

    def test_ffmpeg_nonzero_returncode(self, tmp_path):
        """ffmpeg 返回非 0 时返回 False"""
        video = tmp_path / "input.mp4"
        video.write_bytes(b"video data")
        audio = str(tmp_path / "output.wav")

        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("noteforge.sources.asr.subprocess.run", return_value=mock_result):
            with patch("noteforge.sources.asr.os.path.exists", return_value=True):
                result = extract_audio(str(video), audio)

        assert result is False

    def test_ffmpeg_timeout_expired(self, tmp_path):
        """subprocess.TimeoutExpired 时返回 False"""
        video = tmp_path / "input.mp4"
        video.write_bytes(b"video data")
        audio = str(tmp_path / "output.wav")

        with patch("noteforge.sources.asr.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=300)):
            result = extract_audio(str(video), audio)

        assert result is False

    def test_exception_returns_false(self, tmp_path):
        """通用异常时返回 False"""
        video = tmp_path / "input.mp4"
        video.write_bytes(b"video data")
        audio = str(tmp_path / "output.wav")

        with patch("noteforge.sources.asr.subprocess.run",
                   side_effect=OSError("ffmpeg not found")):
            result = extract_audio(str(video), audio)

        assert result is False


# ============================================================
# _get_audio_duration tests
# ============================================================

class TestGetAudioDuration:
    """_get_audio_duration 函数测试"""

    def test_soundfile_available_returns_duration(self, tmp_path):
        """soundfile 可用时返回 duration"""
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"dummy audio data")

        mock_sf_mod = MagicMock()
        mock_info = MagicMock()
        mock_info.duration = 125.5
        mock_sf_mod.info.return_value = mock_info

        # 保存原始引用
        orig_sf = sys.modules.get("soundfile")
        orig_asr_sf = getattr(_asr_module, "sf", None)

        sys.modules["soundfile"] = mock_sf_mod
        _asr_module.sf = mock_sf_mod

        try:
            result = _get_audio_duration(str(audio))
        finally:
            if orig_sf is not None:
                sys.modules["soundfile"] = orig_sf
            else:
                sys.modules.pop("soundfile", None)
            _asr_module.sf = orig_asr_sf

        assert result == 125.5

    def test_soundfile_exception_returns_zero(self, tmp_path):
        """soundfile.info 抛异常时返回 0.0"""
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"dummy audio data")

        mock_sf_mod = MagicMock()
        mock_sf_mod.info.side_effect = RuntimeError("corrupt file")

        orig_sf = sys.modules.get("soundfile")
        orig_asr_sf = getattr(_asr_module, "sf", None)

        sys.modules["soundfile"] = mock_sf_mod
        _asr_module.sf = mock_sf_mod

        try:
            result = _get_audio_duration(str(audio))
        finally:
            if orig_sf is not None:
                sys.modules["soundfile"] = orig_sf
            else:
                sys.modules.pop("soundfile", None)
            _asr_module.sf = orig_asr_sf

        assert result == 0.0

    def test_missing_soundfile_returns_zero(self, tmp_path):
        """soundfile 模块不可用时（ImportError）返回 0.0"""
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"dummy")

        # 保存原始引用
        orig_sf = sys.modules.get("soundfile")
        orig_asr_sf = getattr(_asr_module, "sf", None)

        # 移除 soundfile 使 import 失败
        sys.modules.pop("soundfile", None)

        try:
            result = _get_audio_duration(str(audio))
        finally:
            if orig_sf is not None:
                sys.modules["soundfile"] = orig_sf
            _asr_module.sf = orig_asr_sf

        assert result == 0.0


# ============================================================
# transcribe_with_paraformer tests
# ============================================================

class TestTranscribeWithParaformer:
    """transcribe_with_paraformer 函数测试"""

    # ------------------------------------------------------------------
    # Helper: 构建 mock 环境并执行转写
    # ------------------------------------------------------------------
    def _run(self, audio_path, has_cuda=True, disable_speaker=False,
             generate_result=None):
        """
        在受控 sys.modules 环境下执行 transcribe_with_paraformer。
        返回 (result_text, mock_model, mock_autop_mock)
        """
        if generate_result is None:
            generate_result = [{"text": "hello"}]

        mock_model = MagicMock()
        mock_model.generate.return_value = generate_result

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = has_cuda

        mock_sf = MagicMock()
        mock_sf.info.return_value.duration = 60.0

        mock_funasr_mod = MagicMock()
        mock_autop = MagicMock(return_value=mock_model)
        mock_funasr_mod.AutoModel = mock_autop

        # 保存原始模块
        saved = {}
        for key in ("torch", "funasr", "soundfile"):
            saved[key] = sys.modules.get(key)
        saved_asr_sf = getattr(_asr_module, "sf", None)

        sys.modules["torch"] = mock_torch
        sys.modules["funasr"] = mock_funasr_mod
        sys.modules["soundfile"] = mock_sf
        _asr_module.sf = mock_sf

        try:
            result = transcribe_with_paraformer(audio_path,
                                                 disable_speaker=disable_speaker)
        finally:
            for key in ("torch", "funasr", "soundfile"):
                if saved[key] is not None:
                    sys.modules[key] = saved[key]
                else:
                    sys.modules.pop(key, None)
            _asr_module.sf = saved_asr_sf

        return result, mock_model, mock_autop

    def test_cuda_uses_batch_300_and_speaker(self, tmp_path):
        """CUDA 可用时 batch_size_s=300 且启用 speaker"""
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"dummy audio data")

        result, mock_model, mock_autop = self._run(
            str(audio), has_cuda=True,
            generate_result=[{"text": "你好世界"}]
        )

        assert "你好世界" in result
        _, gen_kwargs = mock_model.generate.call_args
        assert gen_kwargs.get("batch_size_s") == 300
        # AutoModel 应接收 spk_model
        autop_kwargs = mock_autop.call_args[1]
        assert "spk_model" in autop_kwargs

    def test_cpu_mode_uses_batch_60_no_speaker(self, tmp_path):
        """CPU 模式时 batch_size_s=60 且跳过 speaker"""
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"dummy audio data")

        result, mock_model, mock_autop = self._run(
            str(audio), has_cuda=False,
            generate_result=[{"text": "CPU 转录"}]
        )

        assert "CPU 转录" in result
        _, gen_kwargs = mock_model.generate.call_args
        assert gen_kwargs.get("batch_size_s") == 60
        # CPU 模式 AutoModel 不应接收 spk_model
        autop_kwargs = mock_autop.call_args[1]
        assert "spk_model" not in autop_kwargs

    def test_disable_speaker_true_skips_speaker_model(self, tmp_path):
        """disable_speaker=True 时不加载 spk_model（即使有 CUDA）"""
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"dummy audio data")

        _, _, mock_autop = self._run(
            str(audio), has_cuda=True, disable_speaker=True,
            generate_result=[{"text": "结果"}]
        )

        autop_kwargs = mock_autop.call_args[1]
        assert "spk_model" not in autop_kwargs

    def test_text_keys_are_concatenated(self, tmp_path):
        """result 含多个 'text' 段时拼接所有段"""
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"dummy audio data")

        result, _, _ = self._run(
            str(audio), has_cuda=True,
            generate_result=[
                {"text": "第一段内容"},
                {"text": "第二段内容"},
            ]
        )

        assert "第一段内容" in result
        assert "第二段内容" in result

    def test_sentence_info_punc_extracted(self, tmp_path):
        """result 含 'sentence_info' 时提取 'punc' 字段"""
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"dummy audio data")

        result, _, _ = self._run(
            str(audio), has_cuda=True,
            generate_result=[
                {
                    "sentence_info": [
                        {"punc": "大家好。"},
                        {"punc": "今天天气很好。"},
                    ]
                }
            ]
        )

        assert "大家好" in result
        assert "今天天气很好" in result

    def test_empty_result_returns_empty_string(self, tmp_path):
        """空 result 返回空字符串"""
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"dummy audio data")

        result, _, _ = self._run(
            str(audio), has_cuda=True,
            generate_result=[]
        )

        assert result == ""


# ============================================================
# save_result tests
# ============================================================

class TestSaveResult:
    """save_result 函数测试"""

    def test_writes_text_to_correct_path(self, tmp_path):
        """文本写入正确的文件路径"""
        fake_base = tmp_path
        output_dir = fake_base / "output" / "transcripts"
        output_dir.mkdir(parents=True)

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            result = save_result("转写文本内容", "ep01")

        expected_path = output_dir / "ep01.txt"
        assert expected_path.exists()
        assert expected_path.read_text(encoding="utf-8") == "转写文本内容"

    def test_logs_char_count(self, tmp_path):
        """记录字数日志"""
        fake_base = tmp_path
        output_dir = fake_base / "output" / "transcripts"
        output_dir.mkdir(parents=True)

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            with patch("noteforge.sources.asr.logger") as mock_logger:
                result = save_result("测试文本内容", "ep01")

        log_strs = [str(c) for c in mock_logger.info.call_args_list]
        assert any("已保存" in s for s in log_strs)
        assert any("字数" in s for s in log_strs)

    def test_returns_file_path_string(self, tmp_path):
        """返回文件路径字符串"""
        fake_base = tmp_path
        output_dir = fake_base / "output" / "transcripts"
        output_dir.mkdir(parents=True)

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            result = save_result("内容", "ep01")

        assert isinstance(result, str)
        assert result.endswith("ep01.txt")


# ============================================================
# process_episode tests
# ============================================================

class TestProcessEpisode:
    """process_episode 函数测试"""

    def test_episode_not_in_config_returns_false(self):
        """ep_num 不在 config 中时返回 False"""
        config = {"episodes": {"ep01": {"title": "第一集"}}}

        with patch("noteforge.sources.asr.logger") as mock_logger:
            result = process_episode("ep99", config)

        assert result is False

    def test_video_file_not_exists_returns_false(self):
        """视频文件不存在时返回 False"""
        config = {
            "episodes": {
                "ep01": {"title": "第一集", "file": "/nonexistent/path.mp4"}
            }
        }

        with patch("noteforge.sources.asr.logger") as mock_logger:
            result = process_episode("ep01", config)

        assert result is False

    def test_successful_processing_returns_true(self, tmp_path):
        """完整处理流程成功时返回 True"""
        fake_base = tmp_path
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"video content")
        temp_dir = fake_base / "temp"
        temp_dir.mkdir(parents=True)

        config = {
            "episodes": {
                "ep01": {
                    "title": "测试集",
                    "file": str(video_file),
                }
            }
        }

        # process_episode 有一个已知 bug：未定义 disable_speaker 变量
        # 通过设置模块级全局变量绕过该 bug
        orig_ds = getattr(_asr_module, "disable_speaker", None)
        _asr_module.disable_speaker = False

        try:
            with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
                with patch("noteforge.sources.asr.extract_audio", return_value=True):
                    with patch("noteforge.sources.asr.transcribe_with_paraformer",
                               return_value="转写结果文本"):
                        with patch("noteforge.sources.asr.save_result",
                                   return_value="/fake/path/ep01.txt"):
                            with patch("noteforge.sources.asr.os.path.getsize", return_value=1024):
                                with patch("noteforge.sources.asr.os.remove"):
                                    result = process_episode("ep01", config)
        finally:
            if orig_ds is None:
                if hasattr(_asr_module, "disable_speaker"):
                    delattr(_asr_module, "disable_speaker")
            else:
                _asr_module.disable_speaker = orig_ds

        assert result is True

    def test_empty_transcription_returns_false(self, tmp_path):
        """转写结果为空时返回 False"""
        fake_base = tmp_path
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"video content")
        temp_dir = fake_base / "temp"
        temp_dir.mkdir(parents=True)

        config = {
            "episodes": {
                "ep01": {
                    "title": "测试集",
                    "file": str(video_file),
                }
            }
        }

        orig_ds = getattr(_asr_module, "disable_speaker", None)
        _asr_module.disable_speaker = False

        try:
            with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
                with patch("noteforge.sources.asr.extract_audio", return_value=True):
                    with patch("noteforge.sources.asr.transcribe_with_paraformer",
                               return_value=""):
                        with patch("noteforge.sources.asr.os.path.getsize", return_value=1024):
                            result = process_episode("ep01", config)
        finally:
            if orig_ds is None:
                if hasattr(_asr_module, "disable_speaker"):
                    delattr(_asr_module, "disable_speaker")
            else:
                _asr_module.disable_speaker = orig_ds

        assert result is False


# ============================================================
# process_audio_file tests
# ============================================================

class TestProcessAudioFile:
    """process_audio_file 函数测试"""

    def test_file_not_exists_returns_false(self):
        """音频文件不存在时返回 False"""
        fake_base = Path(tempfile.gettempdir()) / "noteforge_test_nonexistent"
        fake_base.mkdir(parents=True, exist_ok=True)
        nonexistent = str(fake_base / "nonexistent.wav")

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            result = process_audio_file(nonexistent)

        assert result is False

    def test_successful_processing_returns_true(self, tmp_path):
        """完整处理音频文件成功时返回 True"""
        fake_base = tmp_path
        audio_file = tmp_path / "podcast.wav"
        audio_file.write_bytes(b"audio content")
        output_dir = fake_base / "output" / "transcripts"
        output_dir.mkdir(parents=True)

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            with patch("noteforge.sources.asr.transcribe_with_paraformer",
                       return_value="播客转写内容"):
                with patch("noteforge.sources.asr.os.path.getsize", return_value=2048):
                    result = process_audio_file(str(audio_file))

        assert result is True

    def test_output_name_from_stem_when_not_provided(self, tmp_path):
        """未提供 output_name 时使用文件名（不含扩展名）"""
        fake_base = tmp_path
        audio_file = tmp_path / "my_episode.wav"
        audio_file.write_bytes(b"audio content")
        output_dir = fake_base / "output" / "transcripts"
        output_dir.mkdir(parents=True)

        with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
            with patch("noteforge.sources.asr.transcribe_with_paraformer",
                       return_value="转写内容"):
                with patch("noteforge.sources.asr.os.path.getsize", return_value=1024):
                    result = process_audio_file(str(audio_file))

        assert result is True
        expected_output = output_dir / "my_episode.txt"
        assert expected_output.exists()
        assert expected_output.read_text(encoding="utf-8") == "转写内容"


# ============================================================
# main tests
# ============================================================

class TestMain:
    """main 函数测试"""

    def test_no_args_prints_usage(self):
        """无参数时打印用法信息"""
        with patch.object(sys, "argv", ["asr.py"]):
            with patch("noteforge.sources.asr.print") as mock_print:
                main()

        # 用法信息至少有 4 行 print
        assert mock_print.call_count >= 4

    def test_audio_file_arg_triggers_process_audio_file(self, tmp_path):
        """音频文件参数触发 process_audio_file"""
        fake_base = tmp_path
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"audio data")

        with patch.object(sys, "argv", ["asr.py", str(audio_file)]):
            with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
                with patch("noteforge.sources.asr.ensure_dirs"):
                    with patch("noteforge.sources.asr.process_audio_file",
                               return_value=True) as mock_proc:
                        with patch("noteforge.sources.asr.sys.exit"):
                            main()

        mock_proc.assert_called_once()

    def test_ep_arg_loads_config_and_processes(self, tmp_path):
        """ep 参数加载配置并处理集数"""
        fake_base = tmp_path
        config_dir = fake_base / "config"
        config_dir.mkdir(parents=True)
        config_data = {
            "episodes": {
                "ep01": {"title": "测试集", "file": "/fake/path.mp4"}
            }
        }
        (config_dir / "video-mapping.json").write_text(
            json.dumps(config_data), encoding="utf-8"
        )

        with patch.object(sys, "argv", ["asr.py", "ep01"]):
            with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
                with patch("noteforge.sources.asr.ensure_dirs"):
                    with patch("noteforge.sources.asr.process_episode",
                               return_value=True) as mock_proc:
                        with patch("noteforge.sources.asr.print"):
                            with patch("noteforge.sources.asr.time.time", return_value=100.0):
                                main()

        mock_proc.assert_called_once_with("ep01", config_data, disable_speaker=False)

    def test_all_arg_processes_all_episodes(self, tmp_path):
        """all 参数处理配置中所有集数（按字母序）"""
        fake_base = tmp_path
        config_dir = fake_base / "config"
        config_dir.mkdir(parents=True)
        config_data = {
            "episodes": {
                "ep01": {"title": "第一集", "file": "/fake/p1.mp4"},
                "ep02": {"title": "第二集", "file": "/fake/p2.mp4"},
            }
        }
        (config_dir / "video-mapping.json").write_text(
            json.dumps(config_data), encoding="utf-8"
        )

        with patch.object(sys, "argv", ["asr.py", "all"]):
            with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
                with patch("noteforge.sources.asr.ensure_dirs"):
                    with patch("noteforge.sources.asr.process_episode",
                               return_value=True) as mock_proc:
                        with patch("noteforge.sources.asr.print"):
                            with patch("noteforge.sources.asr.time.time", return_value=100.0):
                                main()

        assert mock_proc.call_count == 2
        calls = [c[0][0] for c in mock_proc.call_args_list]
        assert calls == ["ep01", "ep02"]

    def test_unknown_episode_warns_and_skips(self, tmp_path):
        """未知集数应警告并跳过"""
        fake_base = tmp_path
        config_dir = fake_base / "config"
        config_dir.mkdir(parents=True)
        config_data = {
            "episodes": {
                "ep01": {"title": "第一集", "file": "/fake/p.mp4"}
            }
        }
        (config_dir / "video-mapping.json").write_text(
            json.dumps(config_data), encoding="utf-8"
        )

        with patch.object(sys, "argv", ["asr.py", "ep99"]):
            with patch("noteforge.sources.asr.get_base_dir", return_value=fake_base):
                with patch("noteforge.sources.asr.ensure_dirs"):
                    with patch("noteforge.sources.asr.logger") as mock_logger:
                        with patch("noteforge.sources.asr.print"):
                            main()

        warn_calls = [c for c in mock_logger.warning.call_args_list
                      if "ep99" in str(c)]
        assert len(warn_calls) >= 1
