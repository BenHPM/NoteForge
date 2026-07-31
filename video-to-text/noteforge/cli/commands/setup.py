# -*- coding: utf-8 -*-
"""一键环境设置命令"""
import os
import sys
import subprocess
import shutil
from pathlib import Path


def _get_base_dir():
    """获取 video-to-text 根目录"""
    return Path(__file__).parent.parent.parent.parent


def run_setup(args, base_dir=None):
    """一键创建隔离环境：venv + 依赖 + 工具检查 + .env"""
    if base_dir is None:
        base_dir = _get_base_dir()
    base_dir = Path(base_dir)
    venv_dir = base_dir / "envs" / "paraformer"
    requirements = base_dir / "requirements.txt"
    env_example = base_dir.parent / ".env.example"
    env_file = base_dir.parent / ".env"

    results = []

    # 1. 创建 venv
    if venv_dir.exists() and (venv_dir / "Scripts" / "python.exe").exists():
        results.append(("venv", True, f"已存在: {venv_dir}"))
    else:
        print("[1/4] 创建 Python 3.10 隔离环境...")
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                check=True, capture_output=True, text=True,
            )
            # 验证 Python 版本
            py_exe = str(venv_dir / "Scripts" / "python.exe") if sys.platform == "win32" else str(venv_dir / "bin" / "python")
            ver_out = subprocess.run(
                [py_exe, "--version"], capture_output=True, text=True,
            )
            results.append(("venv", True, f"创建成功: {venv_dir} ({ver_out.stdout.strip()})"))
        except subprocess.CalledProcessError as e:
            results.append(("venv", False, f"创建失败: {e.stderr.strip() if e.stderr else e}"))
            _print_summary(results)
            return 1
        except Exception as e:
            results.append(("venv", False, f"创建失败: {e}"))
            _print_summary(results)
            return 1

    # 2. 安装依赖
    py_exe = str(venv_dir / "Scripts" / "python.exe") if sys.platform == "win32" else str(venv_dir / "bin" / "python")
    print("[2/4] 安装 requirements.txt...")
    if requirements.exists():
        try:
            proc = subprocess.run(
                [py_exe, "-m", "pip", "install", "-r", str(requirements)],
                capture_output=True, text=True,
            )
            if proc.returncode == 0:
                results.append(("requirements", True, "依赖安装成功"))
            else:
                results.append(("requirements", False, f"安装失败: {proc.stderr.strip()[:200]}"))
        except Exception as e:
            results.append(("requirements", False, f"安装异常: {e}"))
    else:
        results.append(("requirements", False, f"未找到: {requirements}"))

    # 3. 检查 yt-dlp / ffmpeg
    print("[3/4] 检查系统工具...")
    for tool in ["yt-dlp", "ffmpeg"]:
        found = shutil.which(tool)
        if found:
            results.append((tool, True, f"已安装: {found}"))
        else:
            results.append((tool, False, "未找到，请安装后加入 PATH"))

    # 4. 创建 .env
    print("[4/4] 检查 .env 配置...")
    if env_file.exists():
        results.append((".env", True, f"已存在: {env_file}"))
    elif env_example.exists():
        try:
            shutil.copy2(str(env_example), str(env_file))
            results.append((".env", True, f"已从 .env.example 创建: {env_file}（请填入 API Key）"))
        except Exception as e:
            results.append((".env", False, f"创建失败: {e}"))
    else:
        results.append((".env", False, f".env 和 .env.example 均不存在，请手动创建"))

    _print_summary(results)
    failures = [r for r in results if not r[1]]
    return 1 if failures else 0


def _print_summary(results):
    """打印设置结果摘要"""
    print("\n" + "=" * 50)
    print("  NoteForge 环境设置结果")
    print("=" * 50)
    for name, ok, msg in results:
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {name}: {msg}")
    print("=" * 50)
    failures = [r for r in results if not r[1]]
    if failures:
        print(f"  {len(failures)} 项失败，请按提示修复后重试")
    else:
        print("  全部完成！运行 noteforge --doctor 验证环境")
