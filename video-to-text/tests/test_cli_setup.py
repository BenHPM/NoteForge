# -*- coding: utf-8 -*-
"""CLI setup/doctor/validate_config 命令测试"""
import os
import sys
import types
import tempfile
import shutil
from pathlib import Path
from unittest import mock
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# run_doctor tests
# ---------------------------------------------------------------------------

class TestRunDoctor:
    """run_doctor 环境诊断测试"""

    def _make_args(self):
        """创建模拟 args 对象"""
        return types.SimpleNamespace()

    @patch('shutil.which')
    @patch('subprocess.run')
    def test_all_components_present(self, mock_run, mock_which):
        """所有组件都存在时返回 0"""
        # shutil.which 返回路径
        mock_which.side_effect = lambda name: f"/usr/bin/{name}" if name in ("yt-dlp", "ffmpeg", "lark-cli") else None

        # subprocess.run 返回版本信息
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="yt-dlp 2024.01.01\n",
            stderr="",
        )

        # tiktoken 可导入
        with patch.dict(sys.modules, {'tiktoken': MagicMock()}):
            with tempfile.TemporaryDirectory() as tmpdir:
                base_dir = Path(tmpdir) / "video-to-text"
                base_dir.mkdir()

                # .env 文件存在且包含 API key
                env_file = Path(tmpdir) / ".env"
                env_file.write_text("ANTHROPIC_API_KEY=sk-ant-real-key\n", encoding="utf-8")

                # config 目录存在且包含有效 YAML
                config_dir = base_dir / "config"
                config_dir.mkdir()
                (config_dir / "llm_engine_config.yaml").write_text(
                    "provider:\n  type: claude\nquality:\n  min_score: 0.8\npaths:\n  rules: x\n",
                    encoding="utf-8",
                )
                (config_dir / "note_generation_rules.yaml").write_text(
                    "rules:\n  R1:\n    id: R1\n    severity: fatal\n",
                    encoding="utf-8",
                )

                # venv 目录存在
                venv_dir = base_dir / "envs" / "paraformer"
                venv_dir.mkdir(parents=True)
                py_exe = venv_dir / "Scripts" / "python.exe"
                py_exe.parent.mkdir(parents=True, exist_ok=True)
                py_exe.touch()

                # output 目录存在
                output_dir = base_dir / "output"
                output_dir.mkdir()

                from noteforge.cli.commands.doctor import run_doctor
                result = run_doctor(self._make_args(), base_dir=base_dir)

        # 所有组件存在时应返回 0
        assert result == 0

    @patch('shutil.which')
    def test_missing_components(self, mock_which):
        """缺少组件时返回 1"""
        # 所有工具都找不到
        mock_which.return_value = None

        # tiktoken 不可导入
        with patch.dict(sys.modules, {'tiktoken': None}):
            with tempfile.TemporaryDirectory() as tmpdir:
                base_dir = Path(tmpdir) / "video-to-text"
                base_dir.mkdir()
                # 不创建 .env、config、venv、output

                from noteforge.cli.commands.doctor import run_doctor
                result = run_doctor(self._make_args(), base_dir=base_dir)

        # 缺少组件时应返回 1
        assert result == 1


# ---------------------------------------------------------------------------
# run_validate_config tests
# ---------------------------------------------------------------------------

class TestRunValidateConfig:
    """run_validate_config 配置验证测试"""

    def _make_args(self):
        return types.SimpleNamespace()

    def test_valid_config(self):
        """有效配置返回 0"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "video-to-text"
            base_dir.mkdir()
            config_dir = base_dir / "config"
            config_dir.mkdir()

            # 写入有效的 llm_engine_config.yaml
            (config_dir / "llm_engine_config.yaml").write_text(
                "provider:\n"
                "  type: claude\n"
                "  claude:\n"
                "    model: claude-sonnet-4-20250514\n"
                "    max_tokens: 8192\n"
                "    temperature: 0.3\n"
                "quality:\n"
                "  min_score: 0.80\n"
                "  max_retries: 2\n"
                "paths:\n"
                "  rules: config/note_generation_rules.yaml\n"
                "  transcripts_dir: output/transcripts\n"
                "  notes_dir: output/notes\n"
                "knowledge_domains:\n"
                "  - id: general\n"
                "    name: 其他\n"
                "    match_keywords: []\n"
                "    match_files: []\n"
                "    output_name: 其他笔记-知识体系\n",
                encoding="utf-8",
            )

            # 写入有效的 note_generation_rules.yaml
            (config_dir / "note_generation_rules.yaml").write_text(
                "rules:\n"
                "  R1_禁止虚构数据:\n"
                "    id: R1\n"
                "    severity: fatal\n"
                "  R2_禁止越界增补:\n"
                "    id: R2\n"
                "    severity: fatal\n"
                "  R3_禁止事实反转:\n"
                "    id: R3\n"
                "    severity: fatal\n"
                "key_concepts:\n"
                "  _general: {}\n",
                encoding="utf-8",
            )

            from noteforge.cli.commands.validate_config import run_validate_config
            result = run_validate_config(self._make_args(), base_dir=base_dir)

        assert result == 0

    def test_missing_required_fields(self):
        """缺少必需字段时返回 1"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "video-to-text"
            base_dir.mkdir()
            config_dir = base_dir / "config"
            config_dir.mkdir()

            # 写入缺少 provider/quality/paths 的配置
            (config_dir / "llm_engine_config.yaml").write_text(
                "meta:\n  version: '1.0'\n",
                encoding="utf-8",
            )

            # 写入缺少 rules 的配置
            (config_dir / "note_generation_rules.yaml").write_text(
                "meta:\n  version: '2.0'\n",
                encoding="utf-8",
            )

            from noteforge.cli.commands.validate_config import run_validate_config
            result = run_validate_config(self._make_args(), base_dir=base_dir)

        # 缺少必需字段应返回 1
        assert result == 1

    def test_invalid_quality_threshold(self):
        """quality.min_score 超出范围时返回 1"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "video-to-text"
            base_dir.mkdir()
            config_dir = base_dir / "config"
            config_dir.mkdir()

            (config_dir / "llm_engine_config.yaml").write_text(
                "provider:\n"
                "  type: claude\n"
                "  claude:\n"
                "    model: claude-sonnet-4-20250514\n"
                "    max_tokens: 8192\n"
                "    temperature: 0.3\n"
                "quality:\n"
                "  min_score: 1.5\n"  # 超出 0-1 范围
                "paths:\n"
                "  rules: x\n"
                "  transcripts_dir: x\n"
                "  notes_dir: x\n"
                "knowledge_domains:\n"
                "  - id: general\n"
                "    name: 其他\n"
                "    match_keywords: []\n"
                "    match_files: []\n",
                encoding="utf-8",
            )

            (config_dir / "note_generation_rules.yaml").write_text(
                "rules:\n"
                "  R1:\n"
                "    id: R1\n"
                "    severity: fatal\n"
                "key_concepts:\n"
                "  _general: {}\n",
                encoding="utf-8",
            )

            from noteforge.cli.commands.validate_config import run_validate_config
            result = run_validate_config(self._make_args(), base_dir=base_dir)

        assert result == 1

    def test_knowledge_domains_missing_id(self):
        """knowledge_domains 缺少 id 字段时返回 1"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "video-to-text"
            base_dir.mkdir()
            config_dir = base_dir / "config"
            config_dir.mkdir()

            (config_dir / "llm_engine_config.yaml").write_text(
                "provider:\n"
                "  type: claude\n"
                "  claude:\n"
                "    model: claude-sonnet-4-20250514\n"
                "    max_tokens: 8192\n"
                "    temperature: 0.3\n"
                "quality:\n"
                "  min_score: 0.80\n"
                "paths:\n"
                "  rules: x\n"
                "  transcripts_dir: x\n"
                "  notes_dir: x\n"
                "knowledge_domains:\n"
                "  - name: 无ID域\n"
                "    match_keywords: [test]\n",
                encoding="utf-8",
            )

            (config_dir / "note_generation_rules.yaml").write_text(
                "rules:\n"
                "  R1:\n"
                "    id: R1\n"
                "    severity: fatal\n"
                "key_concepts:\n"
                "  _general: {}\n",
                encoding="utf-8",
            )

            from noteforge.cli.commands.validate_config import run_validate_config
            result = run_validate_config(self._make_args(), base_dir=base_dir)

        assert result == 1

    def test_config_file_not_found(self):
        """配置文件不存在时返回 1"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "video-to-text"
            base_dir.mkdir()
            # 不创建 config 目录

            from noteforge.cli.commands.validate_config import run_validate_config
            result = run_validate_config(self._make_args(), base_dir=base_dir)

        assert result == 1

    def test_invalid_provider_type(self):
        """provider.type 无效时返回 1"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "video-to-text"
            base_dir.mkdir()
            config_dir = base_dir / "config"
            config_dir.mkdir()

            (config_dir / "llm_engine_config.yaml").write_text(
                "provider:\n"
                "  type: invalid_provider\n"
                "quality:\n"
                "  min_score: 0.80\n"
                "paths:\n"
                "  rules: x\n"
                "  transcripts_dir: x\n"
                "  notes_dir: x\n",
                encoding="utf-8",
            )

            (config_dir / "note_generation_rules.yaml").write_text(
                "rules:\n"
                "  R1:\n"
                "    id: R1\n"
                "    severity: fatal\n"
                "key_concepts:\n"
                "  _general: {}\n",
                encoding="utf-8",
            )

            from noteforge.cli.commands.validate_config import run_validate_config
            result = run_validate_config(self._make_args(), base_dir=base_dir)

        assert result == 1


# ---------------------------------------------------------------------------
# run_setup tests
# ---------------------------------------------------------------------------

class TestRunSetup:
    """run_setup 一键环境设置测试"""

    def _make_args(self):
        return types.SimpleNamespace()

    @patch('shutil.which')
    @patch('shutil.copy2')
    @patch('subprocess.run')
    def test_creates_venv(self, mock_run, mock_copy2, mock_which):
        """venv 不存在时创建并安装依赖"""
        mock_run.return_value = MagicMock(returncode=0, stdout="Python 3.10.0\n", stderr="")
        mock_which.side_effect = lambda name: f"/usr/bin/{name}" if name in ("yt-dlp", "ffmpeg") else None

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "video-to-text"
            base_dir.mkdir()
            (base_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")
            (base_dir / "envs").mkdir()
            env_example = Path(tmpdir) / ".env.example"
            env_example.write_text("ANTHROPIC_API_KEY=sk-ant-xxx\n", encoding="utf-8")

            from noteforge.cli.commands.setup import run_setup
            result = run_setup(self._make_args(), base_dir=base_dir)

        # 验证 subprocess.run 被调用（创建 venv + 安装依赖）
        assert mock_run.called
        assert isinstance(result, int)

    @patch('shutil.which')
    @patch('subprocess.run')
    def test_venv_already_exists(self, mock_run, mock_which):
        """venv 已存在时跳过创建"""
        mock_which.side_effect = lambda name: f"/usr/bin/{name}" if name in ("yt-dlp", "ffmpeg") else None
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "video-to-text"
            base_dir.mkdir()
            (base_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")

            # 创建已存在的 venv
            venv_dir = base_dir / "envs" / "paraformer"
            venv_dir.mkdir(parents=True)
            scripts_dir = venv_dir / "Scripts"
            scripts_dir.mkdir()
            (scripts_dir / "python.exe").touch()

            # .env 已存在
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("ANTHROPIC_API_KEY=sk-ant-real\n", encoding="utf-8")

            from noteforge.cli.commands.setup import run_setup
            result = run_setup(self._make_args(), base_dir=base_dir)

        # venv 已存在，所有检查应通过
        assert result == 0

    @patch('subprocess.run')
    def test_venv_creation_failure(self, mock_run):
        """venv 创建失败时返回 1"""
        mock_run.side_effect = Exception("venv creation failed")

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "video-to-text"
            base_dir.mkdir()

            from noteforge.cli.commands.setup import run_setup
            result = run_setup(self._make_args(), base_dir=base_dir)

        assert result == 1

    @patch('shutil.which')
    @patch('subprocess.run')
    def test_missing_tools_returns_failure(self, mock_run, mock_which):
        """缺少 yt-dlp/ffmpeg 时返回 1"""
        mock_run.return_value = MagicMock(returncode=0, stdout="Python 3.10.0\n", stderr="")
        mock_which.return_value = None  # 所有工具都找不到

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "video-to-text"
            base_dir.mkdir()
            (base_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")

            # 创建 venv
            venv_dir = base_dir / "envs" / "paraformer"
            venv_dir.mkdir(parents=True)
            scripts_dir = venv_dir / "Scripts"
            scripts_dir.mkdir()
            (scripts_dir / "python.exe").touch()

            from noteforge.cli.commands.setup import run_setup
            result = run_setup(self._make_args(), base_dir=base_dir)

        # 缺少工具应返回 1
        assert result == 1
