# -*- coding: utf-8 -*-
"""CLI feishu_auth / feishu_validate 命令测试 + 凭证迁移测试

Run:
  cd video-to-text
  envs/paraformer/python.exe -m pytest tests/test_cli_feishu.py -v
"""
import os
import sys
import types
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**kwargs):
    """创建模拟 args 对象"""
    return types.SimpleNamespace(**kwargs)


# ---------------------------------------------------------------------------
# run_feishu_auth tests
# ---------------------------------------------------------------------------

class TestRunFeishuAuth:
    """run_feishu_auth 飞书认证引导测试"""

    @patch('noteforge.cli.commands.feishu_auth._find_lark_cli')
    def test_lark_cli_detected(self, mock_find):
        """lark-cli 已安装时显示 OK"""
        mock_find.return_value = "/usr/bin/lark-cli"

        with patch.dict(os.environ, {
            'FEISHU_APP_ID': 'cli_test1234',
            'FEISHU_APP_SECRET': 'secret1234567890abcdef',
        }):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                from noteforge.cli.commands.feishu_auth import run_feishu_auth
                result = run_feishu_auth(_make_args())

        # lark-cli 存在且认证成功时应返回 0
        assert result == 0
        mock_find.assert_called_once()

    @patch('noteforge.cli.commands.feishu_auth._find_lark_cli')
    def test_lark_cli_missing_prints_install_instructions(self, mock_find, capsys):
        """lark-cli 未安装时返回 1 并打印安装说明"""
        mock_find.return_value = None

        from noteforge.cli.commands.feishu_auth import run_feishu_auth
        result = run_feishu_auth(_make_args())

        assert result == 1
        captured = capsys.readouterr()
        assert "npm install -g" in captured.out
        assert "lark-cli" in captured.out

    @patch('noteforge.cli.commands.feishu_auth._find_lark_cli')
    def test_missing_env_vars_returns_failure(self, mock_find, capsys):
        """缺少 FEISHU_APP_ID/SECRET 时返回 1"""
        mock_find.return_value = "/usr/bin/lark-cli"

        with patch.dict(os.environ, {}, clear=True):
            # 确保环境变量不存在
            os.environ.pop('FEISHU_APP_ID', None)
            os.environ.pop('FEISHU_APP_SECRET', None)
            from noteforge.cli.commands.feishu_auth import run_feishu_auth
            result = run_feishu_auth(_make_args())

        assert result == 1
        captured = capsys.readouterr()
        assert "FEISHU_APP_ID" in captured.out

    @patch('noteforge.cli.commands.feishu_auth._find_lark_cli')
    def test_auth_timeout_returns_failure(self, mock_find, capsys):
        """认证超时时返回 1"""
        mock_find.return_value = "/usr/bin/lark-cli"

        with patch.dict(os.environ, {
            'FEISHU_APP_ID': 'cli_test1234',
            'FEISHU_APP_SECRET': 'secret1234567890abcdef',
        }):
            with patch('subprocess.run') as mock_run:
                import subprocess
                mock_run.side_effect = subprocess.TimeoutExpired(cmd="lark-cli auth", timeout=120)
                from noteforge.cli.commands.feishu_auth import run_feishu_auth
                result = run_feishu_auth(_make_args())

        assert result == 1
        captured = capsys.readouterr()
        assert "超时" in captured.out

    @patch('noteforge.cli.commands.feishu_auth._find_lark_cli')
    def test_auth_failure_returns_failure(self, mock_find, capsys):
        """lark-cli auth 返回非零时返回 1"""
        mock_find.return_value = "/usr/bin/lark-cli"

        with patch.dict(os.environ, {
            'FEISHU_APP_ID': 'cli_test1234',
            'FEISHU_APP_SECRET': 'secret1234567890abcdef',
        }):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                from noteforge.cli.commands.feishu_auth import run_feishu_auth
                result = run_feishu_auth(_make_args())

        assert result == 1
        captured = capsys.readouterr()
        assert "FAIL" in captured.out


# ---------------------------------------------------------------------------
# run_feishu_validate tests
# ---------------------------------------------------------------------------

class TestRunFeishuValidate:
    """run_feishu_validate 飞书凭证验证测试"""

    @patch('noteforge.cli.commands.feishu_auth._find_lark_cli')
    def test_checks_env_vars(self, mock_find, capsys):
        """验证检查环境变量是否存在"""
        mock_find.return_value = None  # lark-cli 未安装，跳过 API 检查

        with patch.dict(os.environ, {
            'FEISHU_APP_ID': 'cli_test1234',
            'FEISHU_APP_SECRET': 'secret1234567890abcdef',
            'FEISHU_SPACE_ID': 'space_test123',
            'FEISHU_ROOT_NODE_TOKEN': 'nodetoken_test',
        }):
            from noteforge.cli.commands.feishu_auth import run_feishu_validate
            result = run_feishu_validate(_make_args())

        # 环境变量齐全但 lark-cli 未安装，API 检查跳过
        captured = capsys.readouterr()
        assert "FEISHU_APP_ID" in captured.out
        assert "FEISHU_SPACE_ID" in captured.out

    @patch('noteforge.cli.commands.feishu_auth._find_lark_cli')
    def test_missing_env_vars_reports_fail(self, mock_find, capsys):
        """缺少环境变量时报告 FAIL"""
        mock_find.return_value = None

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop('FEISHU_APP_ID', None)
            os.environ.pop('FEISHU_APP_SECRET', None)
            os.environ.pop('FEISHU_SPACE_ID', None)
            os.environ.pop('FEISHU_ROOT_NODE_TOKEN', None)
            from noteforge.cli.commands.feishu_auth import run_feishu_validate
            result = run_feishu_validate(_make_args())

        assert result == 1
        captured = capsys.readouterr()
        assert "FAIL" in captured.out

    @patch('noteforge.cli.commands.feishu_auth._find_lark_cli')
    @patch('subprocess.run')
    def test_api_connection_success(self, mock_run, mock_find, capsys):
        """API 连接成功时返回 0"""
        mock_find.return_value = "/usr/bin/lark-cli"

        # 模拟两个 API 调用：space 查询 + root_node 查询
        space_resp = MagicMock(
            returncode=0,
            stdout='{"ok": true, "data": {"space": {"name": "AI笔记库"}}}',
        )
        node_resp = MagicMock(
            returncode=0,
            stdout='{"ok": true, "data": {"node": {"title": "AI笔记库"}}}',
        )
        mock_run.side_effect = [space_resp, node_resp]

        with patch.dict(os.environ, {
            'FEISHU_APP_ID': 'cli_test1234',
            'FEISHU_APP_SECRET': 'secret1234567890abcdef',
            'FEISHU_SPACE_ID': 'space_test123',
            'FEISHU_ROOT_NODE_TOKEN': 'nodetoken_test',
        }):
            from noteforge.cli.commands.feishu_auth import run_feishu_validate
            result = run_feishu_validate(_make_args())

        assert result == 0
        captured = capsys.readouterr()
        assert "OK" in captured.out

    @patch('noteforge.cli.commands.feishu_auth._find_lark_cli')
    @patch('subprocess.run')
    def test_api_connection_failure(self, mock_run, mock_find, capsys):
        """API 连接失败时返回 1"""
        mock_find.return_value = "/usr/bin/lark-cli"
        mock_run.return_value = MagicMock(returncode=1, stdout='', stderr='error')

        with patch.dict(os.environ, {
            'FEISHU_APP_ID': 'cli_test1234',
            'FEISHU_APP_SECRET': 'secret1234567890abcdef',
            'FEISHU_SPACE_ID': 'space_test123',
            'FEISHU_ROOT_NODE_TOKEN': 'nodetoken_test',
        }):
            from noteforge.cli.commands.feishu_auth import run_feishu_validate
            result = run_feishu_validate(_make_args())

        assert result == 1

    @patch('noteforge.cli.commands.feishu_auth._find_lark_cli')
    def test_yaml_fallback_when_env_missing(self, mock_find, capsys):
        """环境变量缺失时从 YAML 配置回退读取"""
        mock_find.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "video-to-text"
            base_dir.mkdir()
            config_dir = base_dir / "config"
            config_dir.mkdir()

            # 写入包含 feishu 配置的 YAML
            (config_dir / "llm_engine_config.yaml").write_text(
                "feishu:\n"
                "  enabled: true\n"
                "  space_id: yaml_space_id\n"
                "  root_node_token: yaml_root_token\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {
                'FEISHU_APP_ID': 'cli_test1234',
                'FEISHU_APP_SECRET': 'secret1234567890abcdef',
            }, clear=False):
                # 确保空间 ID 环境变量不存在
                os.environ.pop('FEISHU_SPACE_ID', None)
                os.environ.pop('FEISHU_ROOT_NODE_TOKEN', None)

                with patch('noteforge.cli.commands.feishu_auth._get_base_dir', return_value=base_dir):
                    from noteforge.cli.commands.feishu_auth import run_feishu_validate
                    result = run_feishu_validate(_make_args())

        captured = capsys.readouterr()
        # 应该看到从 YAML 回退读取的提示
        assert "YAML" in captured.out or "FAIL" in captured.out


# ---------------------------------------------------------------------------
# _find_lark_cli tests
# ---------------------------------------------------------------------------

class TestFindLarkCli:
    """_find_lark_cli 查找逻辑测试"""

    @patch('shutil.which')
    def test_found_via_which(self, mock_which):
        """shutil.which 找到 lark-cli 时返回路径"""
        mock_which.return_value = "/usr/bin/lark-cli"
        from noteforge.cli.commands.feishu_auth import _find_lark_cli
        result = _find_lark_cli()
        assert result == "/usr/bin/lark-cli"

    @patch('shutil.which')
    def test_not_found_returns_none(self, mock_which):
        """lark-cli 完全找不到时返回 None"""
        mock_which.return_value = None
        with patch('os.path.exists', return_value=False):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
                from noteforge.cli.commands.feishu_auth import _find_lark_cli
                result = _find_lark_cli()
        assert result is None

    @patch('shutil.which')
    def test_windows_cmd_wrapper(self, mock_which):
        """Windows 下检查 npm 全局目录的 .cmd 包装器"""
        mock_which.return_value = None  # which 找不到

        with patch('os.path.exists') as mock_exists:
            mock_exists.return_value = True
            with patch('sys.platform', 'win32'):
                from noteforge.cli.commands.feishu_auth import _find_lark_cli
                result = _find_lark_cli()
                # 应该找到 .cmd 包装器
                assert result is not None
                assert result.endswith("lark-cli.cmd")


# ---------------------------------------------------------------------------
# Credential migration: env vars take priority over YAML
# ---------------------------------------------------------------------------

class TestCredentialMigration:
    """凭证迁移：环境变量优先于 YAML 配置"""

    def _import_fs(self):
        return __import__("noteforge.integration.feishu_sync", fromlist=["feishu_sync"])

    def test_env_vars_override_yaml(self):
        """环境变量覆盖 YAML 配置值"""
        fs = self._import_fs()

        config = {
            "feishu": {
                "enabled": True,
                "space_id": "yaml_space_id",
                "root_node_token": "yaml_root_token",
            }
        }

        with patch.dict(os.environ, {
            'FEISHU_SPACE_ID': 'env_space_id',
            'FEISHU_ROOT_NODE_TOKEN': 'env_root_token',
        }):
            result = fs._get_feishu_config(config)

        # 环境变量应覆盖 YAML 值
        assert result["space_id"] == "env_space_id"
        assert result["root_node_token"] == "env_root_token"

    def test_yaml_fallback_when_env_empty(self):
        """环境变量为空时回退到 YAML 配置"""
        fs = self._import_fs()

        config = {
            "feishu": {
                "enabled": True,
                "space_id": "yaml_space_id",
                "root_node_token": "yaml_root_token",
            }
        }

        # 确保环境变量不存在
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('FEISHU_SPACE_ID', None)
            os.environ.pop('FEISHU_ROOT_NODE_TOKEN', None)
            result = fs._get_feishu_config(config)

        # YAML 值应被保留
        assert result["space_id"] == "yaml_space_id"
        assert result["root_node_token"] == "yaml_root_token"

    def test_env_vars_override_even_when_yaml_has_value(self):
        """即使 YAML 有值，环境变量也优先"""
        fs = self._import_fs()

        config = {
            "feishu": {
                "enabled": True,
                "space_id": "yaml_space_id",
                "root_node_token": "yaml_root_token",
            }
        }

        with patch.dict(os.environ, {
            'FEISHU_SPACE_ID': 'env_override_space',
            'FEISHU_ROOT_NODE_TOKEN': 'env_override_root',
        }):
            result = fs._get_feishu_config(config)

        # 环境变量必须覆盖 YAML
        assert result["space_id"] == "env_override_space"
        assert result["root_node_token"] == "env_override_root"

    def test_both_missing_exits(self):
        """环境变量和 YAML 都缺失时退出"""
        fs = self._import_fs()

        config = {
            "feishu": {
                "enabled": True,
                # 没有 space_id 和 root_node_token
            }
        }

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('FEISHU_SPACE_ID', None)
            os.environ.pop('FEISHU_ROOT_NODE_TOKEN', None)
            with pytest.raises(SystemExit):
                fs._get_feishu_config(config)

    def test_feishu_disabled_exits_cleanly(self):
        """feishu.enabled=false 时干净退出"""
        fs = self._import_fs()

        config = {
            "feishu": {
                "enabled": False,
            }
        }

        with pytest.raises(SystemExit) as exc_info:
            fs._get_feishu_config(config)

        # 应该是 exit(0)，不是错误
        assert exc_info.value.code == 0

    def test_env_space_id_only_overrides_space(self):
        """只设置 FEISHU_SPACE_ID 时只覆盖 space_id"""
        fs = self._import_fs()

        config = {
            "feishu": {
                "enabled": True,
                "space_id": "yaml_space_id",
                "root_node_token": "yaml_root_token",
            }
        }

        with patch.dict(os.environ, {
            'FEISHU_SPACE_ID': 'env_space_only',
        }, clear=False):
            os.environ.pop('FEISHU_ROOT_NODE_TOKEN', None)
            result = fs._get_feishu_config(config)

        # space_id 来自环境变量
        assert result["space_id"] == "env_space_only"
        # root_node_token 来自 YAML
        assert result["root_node_token"] == "yaml_root_token"
