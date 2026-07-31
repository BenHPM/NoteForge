# -*- coding: utf-8 -*-
"""
NoteForge ASRProvider + health_check 单元测试

覆盖:
- MockASR: health_check / transcribe
- LocalParaformerASR: health_check（mock funasr 导入）
- ASRTimeoutError
- TranscriptionResult dataclass
- run_health_check（mock 各组件）

运行:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_asr_provider.py -v
"""

import os
import sys
import pytest
import subprocess
from unittest.mock import patch, MagicMock, PropertyMock
from dataclasses import fields

from noteforge.sources.asr_provider import (
    ASRProvider,
    TranscriptionResult,
    ASRTimeoutError,
    LocalParaformerASR,
    MockASR,
)


# ============================================================
# TranscriptionResult dataclass 测试
# ============================================================

class TestTranscriptionResult:
    """TranscriptionResult 数据类测试"""

    def test_default_values(self):
        """默认值正确"""
        result = TranscriptionResult(text="hello")
        assert result.text == "hello"
        assert result.duration_seconds == 0.0
        assert result.language == "zh"
        assert result.speaker_count == 0
        assert result.error is None

    def test_custom_values(self):
        """自定义值正确"""
        result = TranscriptionResult(
            text="测试文本",
            duration_seconds=120.5,
            language="en",
            speaker_count=3,
            error="some error",
        )
        assert result.text == "测试文本"
        assert result.duration_seconds == 120.5
        assert result.language == "en"
        assert result.speaker_count == 3
        assert result.error == "some error"

    def test_fields_exist(self):
        """包含所有预期字段"""
        field_names = {f.name for f in fields(TranscriptionResult)}
        assert field_names == {'text', 'duration_seconds', 'language', 'speaker_count', 'error'}

    def test_empty_text(self):
        """空文本合法"""
        result = TranscriptionResult(text="")
        assert result.text == ""
        assert result.error is None


# ============================================================
# ASRTimeoutError 测试
# ============================================================

class TestASRTimeoutError:
    """ASRTimeoutError 异常测试"""

    def test_is_exception(self):
        """是 Exception 子类"""
        assert issubclass(ASRTimeoutError, Exception)

    def test_raise_and_catch(self):
        """可正常抛出和捕获"""
        with pytest.raises(ASRTimeoutError, match="超时"):
            raise ASRTimeoutError("ASR 转写超时 (7200s)")

    def test_message(self):
        """错误消息正确"""
        err = ASRTimeoutError("timeout at 100s")
        assert str(err) == "timeout at 100s"


# ============================================================
# ASRProvider ABC 测试
# ============================================================

class TestASRProviderABC:
    """ASRProvider 抽象基类测试"""

    def test_cannot_instantiate(self):
        """不能直接实例化 ABC"""
        with pytest.raises(TypeError):
            ASRProvider()

    def test_required_methods(self):
        """子类必须实现 health_check / transcribe / name"""
        # 缺少 transcribe
        with pytest.raises(TypeError):
            type('BadProvider', (ASRProvider,), {
                'health_check': lambda self: (True, 'ok'),
                'name': property(lambda self: 'bad'),
            })()

        # 缺少 health_check
        with pytest.raises(TypeError):
            type('BadProvider2', (ASRProvider,), {
                'transcribe': lambda self, p, t=7200: TranscriptionResult(text=""),
                'name': property(lambda self: 'bad2'),
            })()

    def test_complete_implementation(self):
        """完整实现可以实例化"""
        class GoodProvider(ASRProvider):
            @property
            def name(self) -> str:
                return "GoodProvider"

            def health_check(self) -> tuple[bool, str]:
                return True, "ok"

            def transcribe(self, audio_path: str, timeout: int = 7200) -> TranscriptionResult:
                return TranscriptionResult(text="test")

        provider = GoodProvider()
        assert provider.name == "GoodProvider"
        assert provider.health_check() == (True, "ok")
        result = provider.transcribe("test.wav")
        assert result.text == "test"


# ============================================================
# MockASR 测试
# ============================================================

class TestMockASR:
    """MockASR 测试用 mock 提供商"""

    def setup_method(self):
        self.mock = MockASR()

    def test_name(self):
        """name 属性返回 MockASR"""
        assert self.mock.name == "MockASR"

    def test_health_check_healthy(self):
        """health_check 始终返回健康"""
        ok, msg = self.mock.health_check()
        assert ok is True
        assert "MockASR" in msg

    def test_transcribe_returns_text(self):
        """transcribe 返回固定中文文本"""
        result = self.mock.transcribe("any_audio.wav")
        assert isinstance(result, TranscriptionResult)
        assert result.text == MockASR._MOCK_TEXT
        assert result.language == "zh"
        assert result.speaker_count == 1
        assert result.duration_seconds == 10.0
        assert result.error is None

    def test_transcribe_ignores_audio_path(self):
        """transcribe 不检查音频文件是否存在"""
        result = self.mock.transcribe("/nonexistent/path.wav")
        assert result.text == MockASR._MOCK_TEXT

    def test_transcribe_ignores_timeout(self):
        """transcribe 忽略 timeout 参数"""
        result = self.mock.transcribe("test.wav", timeout=1)
        assert result.text == MockASR._MOCK_TEXT

    def test_is_asr_provider(self):
        """是 ASRProvider 子类"""
        assert isinstance(self.mock, ASRProvider)


# ============================================================
# LocalParaformerASR 测试
# ============================================================

class TestLocalParaformerASR:
    """LocalParaformerASR 测试（mock 子进程调用）"""

    def test_name(self):
        """name 属性返回 LocalParaformerASR"""
        asr = LocalParaformerASR(python_path="/fake/python.exe")
        assert asr.name == "LocalParaformerASR"

    def test_is_asr_provider(self):
        """是 ASRProvider 子类"""
        asr = LocalParaformerASR(python_path="/fake/python.exe")
        assert isinstance(asr, ASRProvider)

    def test_health_check_python_not_found(self):
        """python.exe 不存在时返回不健康"""
        asr = LocalParaformerASR(python_path="/nonexistent/python.exe")
        ok, msg = asr.health_check()
        assert ok is False
        assert "不存在" in msg

    @patch('os.path.isfile', return_value=True)
    @patch('noteforge.sources.asr_provider.subprocess.run')
    def test_health_check_python_version_ok_funasr_ok(self, mock_run, mock_isfile):
        """Python 3.10 + funasr 可导入 → 健康"""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="3.10.12 (main, ...)\n"),
            MagicMock(returncode=0, stdout="1.0.0\n"),
        ]
        asr = LocalParaformerASR(python_path="/fake/python.exe")
        ok, msg = asr.health_check()
        assert ok is True
        assert "3.10" in msg

    @patch('os.path.isfile', return_value=True)
    @patch('noteforge.sources.asr_provider.subprocess.run')
    def test_health_check_wrong_python_version(self, mock_run, mock_isfile):
        """Python 版本不是 3.10 → 不健康"""
        mock_run.return_value = MagicMock(returncode=0, stdout="3.11.5 (main, ...)\n")
        asr = LocalParaformerASR(python_path="/fake/python.exe")
        ok, msg = asr.health_check()
        assert ok is False
        assert "3.10" in msg or "版本" in msg

    @patch('os.path.isfile', return_value=True)
    @patch('noteforge.sources.asr_provider.subprocess.run')
    def test_health_check_funasr_import_fails(self, mock_run, mock_isfile):
        """funasr 导入失败 → 不健康"""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="3.10.12 (main, ...)\n"),
            MagicMock(returncode=1, stderr="ModuleNotFoundError: No module named 'funasr'"),
        ]
        asr = LocalParaformerASR(python_path="/fake/python.exe")
        ok, msg = asr.health_check()
        assert ok is False
        assert "funasr" in msg.lower()

    @patch('os.path.isfile', return_value=True)
    @patch('noteforge.sources.asr_provider.subprocess.run')
    def test_health_check_python_execution_fails(self, mock_run, mock_isfile):
        """Python 执行失败 → 不健康"""
        mock_run.return_value = MagicMock(returncode=1, stderr="Fatal error")
        asr = LocalParaformerASR(python_path="/fake/python.exe")
        ok, msg = asr.health_check()
        assert ok is False

    @patch('os.path.isfile', return_value=True)
    @patch('noteforge.sources.asr_provider.subprocess.run')
    def test_health_check_timeout(self, mock_run, mock_isfile):
        """子进程超时 → 不健康"""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="python", timeout=10)
        asr = LocalParaformerASR(python_path="/fake/python.exe")
        ok, msg = asr.health_check()
        assert ok is False
        assert "超时" in msg

    def test_transcribe_file_not_found(self):
        """音频文件不存在 → FileNotFoundError"""
        asr = LocalParaformerASR(python_path="/fake/python.exe")
        with pytest.raises(FileNotFoundError, match="音频文件不存在"):
            asr.transcribe("/nonexistent/audio.wav")

    @patch('os.path.isfile', return_value=True)
    @patch('noteforge.sources.asr._get_audio_duration', return_value=300.0)
    @patch('noteforge.sources.asr.transcribe_with_paraformer', return_value="这是一段转写文本")
    def test_transcribe_success(self, mock_transcribe, mock_duration, mock_isfile):
        """正常转写返回 TranscriptionResult"""
        asr = LocalParaformerASR(python_path="/fake/python.exe")
        result = asr.transcribe("test.wav")

        assert isinstance(result, TranscriptionResult)
        assert result.text == "这是一段转写文本"
        assert result.duration_seconds == 300.0
        assert result.error is None

    @patch('os.path.isfile', return_value=True)
    @patch('noteforge.sources.asr._get_audio_duration', return_value=100.0)
    @patch('noteforge.sources.asr.transcribe_with_paraformer')
    def test_transcribe_timeout_raises(self, mock_transcribe, mock_duration, mock_isfile):
        """转写超时 → ASRTimeoutError"""
        mock_transcribe.side_effect = Exception("Operation timed out after 7200s")

        asr = LocalParaformerASR(python_path="/fake/python.exe")
        with pytest.raises(ASRTimeoutError, match="超时"):
            asr.transcribe("test.wav", timeout=7200)

    @patch('os.path.isfile', return_value=True)
    @patch('noteforge.sources.asr._get_audio_duration', return_value=50.0)
    @patch('noteforge.sources.asr.transcribe_with_paraformer')
    def test_transcribe_other_error_returns_result(self, mock_transcribe, mock_duration, mock_isfile):
        """非超时错误 → 返回带 error 的 TranscriptionResult"""
        mock_transcribe.side_effect = RuntimeError("GPU OOM")

        asr = LocalParaformerASR(python_path="/fake/python.exe")
        result = asr.transcribe("test.wav")

        assert result.text == ""
        assert result.duration_seconds == 50.0
        assert result.error is not None
        assert "GPU OOM" in result.error


# ============================================================
# run_health_check 测试
# ============================================================

class TestRunHealthCheck:
    """run_health_check 集成测试（mock 各组件）"""

    def test_check_python_ok(self):
        """python 组件检查成功"""
        from noteforge.infra.health_check import _check_python
        ok, msg = _check_python()
        # 在测试环境中 tiktoken 应该可用
        assert ok is True
        assert "Python" in msg

    def test_check_config_ok(self):
        """config 组件检查成功"""
        from noteforge.infra.health_check import _check_config
        ok, msg = _check_config()
        # 配置文件应存在
        assert ok is True
        assert "配置" in msg

    def test_check_config_missing_file(self):
        """config 组件检查 — 配置文件缺失"""
        from noteforge.infra.health_check import _check_config
        with patch('pathlib.Path.exists', return_value=False):
            ok, msg = _check_config()
            assert ok is False
            assert "不存在" in msg

    @patch('noteforge.infra.health_check._check_asr')
    @patch('noteforge.infra.health_check._check_python')
    def test_run_specific_components(self, mock_python, mock_asr):
        """仅检查指定组件"""
        mock_python.return_value = (True, "Python OK")
        mock_asr.return_value = (True, "ASR OK")

        from noteforge.infra.health_check import run_health_check
        results = run_health_check(['python', 'asr'])

        assert 'python' in results
        assert 'asr' in results
        assert 'llm' not in results
        assert 'feishu' not in results
        assert 'config' not in results

    @patch('noteforge.infra.health_check._check_asr')
    @patch('noteforge.infra.health_check._check_python')
    @patch('noteforge.infra.health_check._check_llm')
    @patch('noteforge.infra.health_check._check_feishu')
    @patch('noteforge.infra.health_check._check_config')
    def test_run_all_components(self, mock_config, mock_feishu, mock_llm, mock_asr, mock_python):
        """检查全部组件"""
        mock_python.return_value = (True, "Python OK")
        mock_asr.return_value = (True, "ASR OK")
        mock_llm.return_value = (True, "LLM OK")
        mock_feishu.return_value = (True, "Feishu OK")
        mock_config.return_value = (True, "Config OK")

        from noteforge.infra.health_check import run_health_check
        results = run_health_check()

        assert len(results) == 5
        for component in ('python', 'asr', 'llm', 'feishu', 'config'):
            assert component in results
            ok, msg = results[component]
            assert ok is True

    def test_unknown_component(self):
        """未知组件名返回不健康"""
        from noteforge.infra.health_check import run_health_check
        results = run_health_check(['unknown_component'])
        ok, msg = results['unknown_component']
        assert ok is False
        assert "未知" in msg

    @patch('noteforge.infra.health_check._check_python')
    def test_check_exception_handled(self, mock_python):
        """检查函数抛异常时被捕获"""
        mock_python.side_effect = RuntimeError("unexpected")

        from noteforge.infra.health_check import run_health_check
        results = run_health_check(['python'])
        ok, msg = results['python']
        assert ok is False
        assert "异常" in msg

    @patch.dict(os.environ, {'CI': '1'})
    def test_asr_uses_mock_in_ci(self):
        """CI 环境使用 MockASR"""
        from noteforge.infra.health_check import _check_asr
        ok, msg = _check_asr()
        assert ok is True
        assert "MockASR" in msg

    @patch.dict(os.environ, {'NOTEFORGE_TEST': '1'})
    def test_asr_uses_mock_in_test_env(self):
        """NOTEFORGE_TEST 环境使用 MockASR"""
        from noteforge.infra.health_check import _check_asr
        ok, msg = _check_asr()
        assert ok is True
        assert "MockASR" in msg


# ============================================================
# LLMProvider.health_check 测试
# ============================================================

class TestLLMProviderHealthCheck:
    """LLMProvider.health_check 测试"""

    def test_claude_health_check_success(self):
        """ClaudeProvider health_check — API 返回 200"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider.__new__(ClaudeProvider)
        provider.model = "claude-sonnet-4-20250514"
        provider.base_url = "https://api.anthropic.com"
        provider.api_key = "test-key"
        provider._using_direct_api = True

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch('noteforge.core.llm_providers.requests.post', return_value=mock_resp):
            ok, msg = provider.health_check()
            assert ok is True
            assert "可用" in msg

    def test_claude_health_check_auth_error(self):
        """ClaudeProvider health_check — 401"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider.__new__(ClaudeProvider)
        provider.model = "claude-sonnet-4-20250514"
        provider.base_url = "https://api.anthropic.com"
        provider.api_key = "bad-key"

        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch('noteforge.core.llm_providers.requests.post', return_value=mock_resp):
            ok, msg = provider.health_check()
            assert ok is False
            assert "key" in msg.lower() or "401" in msg

    def test_claude_health_check_connection_error(self):
        """ClaudeProvider health_check — 连接失败"""
        from noteforge.core.llm_providers import ClaudeProvider
        import requests as req
        provider = ClaudeProvider.__new__(ClaudeProvider)
        provider.model = "claude-sonnet-4-20250514"
        provider.base_url = "https://api.anthropic.com"
        provider.api_key = "test-key"

        with patch('noteforge.core.llm_providers.requests.post', side_effect=req.ConnectionError("refused")):
            ok, msg = provider.health_check()
            assert ok is False
            assert "连接" in msg

    def test_claude_health_check_rate_limited(self):
        """ClaudeProvider health_check — 429 限流但视为可用"""
        from noteforge.core.llm_providers import ClaudeProvider
        provider = ClaudeProvider.__new__(ClaudeProvider)
        provider.model = "claude-sonnet-4-20250514"
        provider.base_url = "https://api.anthropic.com"
        provider.api_key = "test-key"

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with patch('noteforge.core.llm_providers.requests.post', return_value=mock_resp):
            ok, msg = provider.health_check()
            assert ok is True
            assert "限流" in msg

    def test_openai_health_check_success(self):
        """OpenAIProvider health_check — API 返回 200"""
        from noteforge.core.llm_providers import OpenAIProvider
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model = "gpt-4o"
        provider.base_url = "https://api.openai.com/v1"
        provider.api_key = "test-key"

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch('noteforge.core.llm_providers.requests.post', return_value=mock_resp):
            ok, msg = provider.health_check()
            assert ok is True
            assert "可用" in msg

    def test_local_health_check_connection_error(self):
        """LocalProvider health_check — 连接失败"""
        from noteforge.core.llm_providers import LocalProvider
        import requests as req
        provider = LocalProvider.__new__(LocalProvider)
        provider.model = "qwen2.5-72b"
        provider.base_url = "http://localhost:11434/v1"

        with patch('noteforge.core.llm_providers.requests.get', side_effect=req.ConnectionError("refused")):
            ok, msg = provider.health_check()
            assert ok is False
            assert "不可达" in msg

    def test_local_health_check_success(self):
        """LocalProvider health_check — 连接成功"""
        from noteforge.core.llm_providers import LocalProvider
        provider = LocalProvider.__new__(LocalProvider)
        provider.model = "qwen2.5-72b"
        provider.base_url = "http://localhost:11434/v1"

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch('noteforge.core.llm_providers.requests.get', return_value=mock_resp):
            ok, msg = provider.health_check()
            assert ok is True
