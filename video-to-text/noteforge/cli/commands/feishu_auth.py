# -*- coding: utf-8 -*-
"""飞书认证与凭证验证 CLI 命令"""
import os
import sys
import shutil
import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger('noteforge.cli.feishu_auth')


def _get_base_dir() -> Path:
    """获取 video-to-text 根目录"""
    return Path(__file__).parent.parent.parent.parent


def _load_env_file(env_path: Optional[Path] = None) -> None:
    """从 .env 文件加载环境变量（不覆盖已有的）。"""
    if env_path is None:
        env_path = _get_base_dir().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def _find_lark_cli() -> Optional[str]:
    """查找 lark-cli 可执行文件路径，未找到返回 None。"""
    # 1. shutil.which（跨平台）
    found = shutil.which("lark-cli")
    if found:
        return found

    # 2. Windows: 检查 npm 全局目录下的 .cmd 包装器
    if sys.platform == "win32":
        npm_global = os.path.expandvars(r"%APPDATA%\npm")
        cmd_path = os.path.join(npm_global, "lark-cli.cmd")
        if os.path.exists(cmd_path):
            return cmd_path

    # 3. npm list -g 检查
    try:
        result = subprocess.run(
            ["npm", "list", "-g", "@anthropic-ai/lark-cli"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and "lark-cli" in result.stdout:
            # npm 确认安装了，但 which 找不到——可能是 PATH 问题
            # 尝试直接用 npx
            return "npx"
    except Exception:
        pass

    return None


def run_feishu_auth(args) -> int:
    """引导 lark-cli 认证流程。

    Returns:
        0 = 成功, 1 = 失败/需要手动操作
    """
    print("\n" + "=" * 60)
    print("  NoteForge 飞书认证引导")
    print("=" * 60)

    # 1. 检查 lark-cli 是否安装
    lark_path = _find_lark_cli()

    if lark_path is None:
        print("\n  [FAIL] lark-cli 未安装")
        print("\n  安装方法:")
        print("    npm install -g @anthropic-ai/lark-cli")
        print("\n  如果没有 npm，请先安装 Node.js:")
        print("    winget install OpenJS.NodeJS.LTS")
        print("\n  安装后重新运行: python -m noteforge --feishu-auth")
        print("=" * 60)
        return 1

    print(f"\n  [OK] lark-cli 已找到: {lark_path}")

    # 2. 检查 .env 中的凭证
    _load_env_file()
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")

    if not app_id or not app_secret:
        print("\n  [WARN] .env 中缺少飞书应用凭证")
        print("\n  请在 .env 文件中设置:")
        print("    FEISHU_APP_ID=cli_xxxxxxxx")
        print("    FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        print("\n  获取方法:")
        print("    1. 访问 https://open.feishu.cn/app")
        print("    2. 创建企业自建应用")
        print("    3. 在「凭证与基础信息」页面获取 App ID 和 App Secret")
        print("    4. 在「权限管理」中开通 wiki:wiki 等所需权限")
        print("=" * 60)
        return 1

    # 脱敏显示
    masked_id = app_id[:6] + "****" if len(app_id) > 6 else "****"
    masked_secret = app_secret[:4] + "****" if len(app_secret) > 4 else "****"
    print(f"  [OK] FEISHU_APP_ID: {masked_id}")
    print(f"  [OK] FEISHU_APP_SECRET: {masked_secret}")

    # 3. 执行 lark-cli auth
    print("\n  正在启动 lark-cli 认证流程...")
    print("  （请在浏览器中完成授权）\n")

    try:
        cmd = [lark_path, "auth"]
        if lark_path == "npx":
            cmd = ["npx", "@anthropic-ai/lark-cli", "auth"]

        result = subprocess.run(
            cmd,
            text=True,
            timeout=120,
        )

        if result.returncode == 0:
            print("\n  [OK] lark-cli 认证成功！")
            print("\n  下一步: 运行验证命令确认连接")
            print("    python -m noteforge --feishu-validate")
            print("=" * 60)
            return 0
        else:
            print(f"\n  [FAIL] lark-cli 认证失败 (exit code: {result.returncode})")
            print("  请检查:")
            print("    1. FEISHU_APP_ID 和 FEISHU_APP_SECRET 是否正确")
            print("    2. 应用是否已发布（或在测试企业中）")
            print("    3. 网络是否可访问飞书 API")
            print("=" * 60)
            return 1

    except subprocess.TimeoutExpired:
        print("\n  [FAIL] 认证超时（120秒）")
        print("  可能原因: 浏览器授权窗口未完成")
        print("  请重新运行: python -m noteforge --feishu-auth")
        print("=" * 60)
        return 1
    except FileNotFoundError:
        print(f"\n  [FAIL] 无法执行 lark-cli: {lark_path}")
        print("  请确认 lark-cli 已正确安装并在 PATH 中")
        print("=" * 60)
        return 1
    except Exception as e:
        print(f"\n  [FAIL] 认证过程异常: {e}")
        print("=" * 60)
        return 1


def run_feishu_validate(args) -> int:
    """验证飞书凭证和连接。

    Returns:
        0 = 全部通过, 1 = 存在问题
    """
    print("\n" + "=" * 60)
    print("  NoteForge 飞书凭证验证")
    print("=" * 60)

    checks = []

    # 1. 加载 .env
    _load_env_file()

    # 2. 检查环境变量
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    space_id = os.environ.get("FEISHU_SPACE_ID", "")
    root_node_token = os.environ.get("FEISHU_ROOT_NODE_TOKEN", "")

    # 脱敏显示
    def _mask(val, show=4):
        if not val:
            return "(空)"
        if len(val) <= show:
            return "****"
        return val[:show] + "****"

    checks.append((
        "FEISHU_APP_ID",
        bool(app_id),
        _mask(app_id, 6),
        "在 .env 中设置 FEISHU_APP_ID",
    ))
    checks.append((
        "FEISHU_APP_SECRET",
        bool(app_secret),
        _mask(app_secret),
        "在 .env 中设置 FEISHU_APP_SECRET",
    ))
    checks.append((
        "FEISHU_SPACE_ID",
        bool(space_id),
        _mask(space_id, 6),
        "在 .env 中设置 FEISHU_SPACE_ID",
    ))
    checks.append((
        "FEISHU_ROOT_NODE_TOKEN",
        bool(root_node_token),
        _mask(root_node_token),
        "在 .env 中设置 FEISHU_ROOT_NODE_TOKEN",
    ))

    # 3. 检查 lark-cli
    lark_path = _find_lark_cli()
    checks.append((
        "lark-cli",
        lark_path is not None,
        lark_path or "未安装",
        "npm install -g @anthropic-ai/lark-cli",
    ))

    # 4. 如果环境变量不全，尝试从 YAML 配置回退读取
    if not space_id or not root_node_token:
        try:
            from noteforge.config import load_yaml
            config_path = _get_base_dir() / "config" / "llm_engine_config.yaml"
            if config_path.exists():
                config = load_yaml(str(config_path))
                feishu_cfg = config.get("feishu", {})
                yaml_space = feishu_cfg.get("space_id", "")
                yaml_root = feishu_cfg.get("root_node_token", "")
                if yaml_space and not space_id:
                    space_id = yaml_space
                    print(f"  [INFO] FEISHU_SPACE_ID 从 YAML 配置回退读取: {_mask(space_id, 6)}")
                if yaml_root and not root_node_token:
                    root_node_token = yaml_root
                    print(f"  [INFO] FEISHU_ROOT_NODE_TOKEN 从 YAML 配置回退读取: {_mask(root_node_token)}")
        except Exception as e:
            logger.debug(f"YAML 配置读取失败: {e}")

    # 5. 尝试连接飞书 API（验证 space_id 和 root_node_token 可访问）
    api_ok = False
    api_detail = ""
    if lark_path and space_id and root_node_token:
        try:
            cmd = [lark_path, "api", "--as", "user",
                   "GET", f"wiki/v2/spaces/{space_id}"]
            if lark_path == "npx":
                cmd = ["npx", "@anthropic-ai/lark-cli", "api", "--as", "user",
                       "GET", f"wiki/v2/spaces/{space_id}"]

            result = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
            if result.returncode == 0:
                import json
                try:
                    resp = json.loads(result.stdout)
                    if resp.get("ok") or resp.get("code") == 0:
                        api_ok = True
                        space_name = resp.get("data", {}).get("space", {}).get("name", "未知")
                        api_detail = f"知识库可访问: {space_name}"
                    else:
                        code = resp.get("code", "unknown")
                        msg = resp.get("msg", "")
                        api_detail = f"API 返回错误: code={code}, msg={msg}"
                except json.JSONDecodeError:
                    api_detail = f"API 返回非 JSON: {result.stdout[:100]}"
            else:
                api_detail = f"API 调用失败 (exit code: {result.returncode})"
        except subprocess.TimeoutExpired:
            api_detail = "API 调用超时（30秒）"
        except Exception as e:
            api_detail = f"API 调用异常: {e}"
    elif not lark_path:
        api_detail = "跳过（lark-cli 未安装）"
    else:
        api_detail = "跳过（缺少 space_id 或 root_node_token）"

    checks.append((
        "飞书 API 连接",
        api_ok,
        api_detail,
        "检查 lark-cli 认证状态和网络连接",
    ))

    # 6. 验证 root_node_token 可访问
    root_ok = False
    root_detail = ""
    if api_ok and root_node_token:
        try:
            cmd = [lark_path, "api", "--as", "user",
                   "GET", f"wiki/v2/spaces/{space_id}/nodes/{root_node_token}"]
            if lark_path == "npx":
                cmd = ["npx", "@anthropic-ai/lark-cli", "api", "--as", "user",
                       "GET", f"wiki/v2/spaces/{space_id}/nodes/{root_node_token}"]

            result = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
            if result.returncode == 0:
                import json
                try:
                    resp = json.loads(result.stdout)
                    if resp.get("ok") or resp.get("code") == 0:
                        root_ok = True
                        node_title = resp.get("data", {}).get("node", {}).get("title", "未知")
                        root_detail = f"根节点可访问: {node_title}"
                    else:
                        root_detail = f"根节点不可访问: {resp.get('msg', 'unknown')}"
                except json.JSONDecodeError:
                    root_detail = f"API 返回非 JSON: {result.stdout[:100]}"
            else:
                root_detail = f"根节点查询失败 (exit code: {result.returncode})"
        except Exception as e:
            root_detail = f"根节点查询异常: {e}"
    elif not api_ok:
        root_detail = "跳过（API 连接失败）"
    else:
        root_detail = "跳过（缺少 root_node_token）"

    checks.append((
        "根节点可访问",
        root_ok,
        root_detail,
        "确认 root_node_token 正确且有访问权限",
    ))

    # 打印结果
    print()
    has_fail = False
    for name, ok, detail, fix in checks:
        if ok is True:
            symbol = "OK"
        elif ok is False:
            symbol = "FAIL"
            has_fail = True
        else:
            symbol = "WARN"
        line = f"  [{symbol}] {name}: {detail}"
        if fix and not ok:
            line += f"\n         修复: {fix}"
        print(line)

    print("\n" + "=" * 60)
    ok_count = sum(1 for _, ok, _, _ in checks if ok is True)
    fail_count = sum(1 for _, ok, _, _ in checks if ok is False)
    print(f"  结果: {ok_count} OK, {fail_count} FAIL")

    if fail_count > 0:
        print("  请按修复建议解决 FAIL 项后重新运行 --feishu-validate")
        return 1
    else:
        print("  飞书凭证验证通过！可以正常使用飞书同步功能。")
        return 0
