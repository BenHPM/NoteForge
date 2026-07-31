# -*- coding: utf-8 -*-
"""环境诊断命令"""
import os
import sys
import shutil
import subprocess
from pathlib import Path


def _get_base_dir():
    """获取 video-to-text 根目录"""
    return Path(__file__).parent.parent.parent.parent


def run_doctor(args, base_dir=None):
    """诊断环境：逐项检查依赖和配置，输出 OK/FAIL + 修复建议"""
    if base_dir is None:
        base_dir = _get_base_dir()
    base_dir = Path(base_dir)
    checks = []

    # 1. Python 3.10
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    is_310 = sys.version_info.major == 3 and sys.version_info.minor == 10
    checks.append((
        "Python 3.10",
        is_310,
        f"当前: {py_version} ({sys.executable})",
        "安装 Python 3.10: winget install Python.Python.3.10" if not is_310 else "",
    ))

    # 2. tiktoken
    try:
        import tiktoken  # noqa: F401
        checks.append(("tiktoken", True, "已安装", ""))
    except ImportError:
        checks.append(("tiktoken", False, "未安装", "pip install tiktoken"))

    # 3. yt-dlp
    yt_dlp_path = shutil.which("yt-dlp")
    if yt_dlp_path:
        try:
            ver = subprocess.run(
                ["yt-dlp", "--version"], capture_output=True, text=True, timeout=5,
            )
            checks.append(("yt-dlp", True, f"已安装: {ver.stdout.strip()}", ""))
        except Exception:
            checks.append(("yt-dlp", True, f"已安装: {yt_dlp_path}", ""))
    else:
        checks.append(("yt-dlp", False, "未找到", "pip install yt-dlp 或 winget install yt-dlp"))

    # 4. ffmpeg
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        try:
            ver = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, text=True, timeout=5,
            )
            first_line = ver.stdout.split("\n")[0] if ver.stdout else ffmpeg_path
            checks.append(("ffmpeg", True, f"已安装: {first_line}", ""))
        except Exception:
            checks.append(("ffmpeg", True, f"已安装: {ffmpeg_path}", ""))
    else:
        checks.append(("ffmpeg", False, "未找到", "winget install ffmpeg 或 https://ffmpeg.org/download.html"))

    # 5. lark-cli（可选）
    lark_path = shutil.which("lark-cli")
    if lark_path:
        checks.append(("lark-cli", True, f"已安装: {lark_path}", ""))
    else:
        checks.append(("lark-cli", None, "未安装（可选，飞书同步需要）", "npm install -g @anthropic-ai/lark-cli"))

    # 6. .env 文件
    env_file = base_dir.parent / ".env"
    if env_file.exists():
        # 检查关键变量是否已填
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                content = f.read()
            has_key = "sk-ant-" in content and "sk-ant-xxx" not in content
            if has_key:
                checks.append((".env", True, f"已配置: {env_file}", ""))
            else:
                checks.append((".env", False, f"存在但 API Key 未填: {env_file}", "编辑 .env 填入 ANTHROPIC_API_KEY"))
        except Exception:
            checks.append((".env", False, f"读取失败: {env_file}", "检查文件权限"))
    else:
        checks.append((".env", False, "不存在", f"cp {base_dir.parent / '.env.example'} {env_file} 并填入 API Key"))

    # 7. 配置文件
    config_dir = base_dir / "config"
    for cfg_name in ["llm_engine_config.yaml", "note_generation_rules.yaml"]:
        cfg_path = config_dir / cfg_name
        if cfg_path.exists():
            try:
                import yaml
                with open(cfg_path, "r", encoding="utf-8") as f:
                    yaml.safe_load(f)
                checks.append((cfg_name, True, f"有效 YAML: {cfg_path}", ""))
            except ImportError:
                checks.append((cfg_name, None, f"存在但无法验证（缺 PyYAML）: {cfg_path}", "pip install PyYAML"))
            except Exception as e:
                checks.append((cfg_name, False, f"YAML 解析错误: {e}", f"修复 {cfg_path}"))
        else:
            checks.append((cfg_name, False, f"不存在: {cfg_path}", f"创建或恢复 {cfg_path}"))

    # 8. Paraformer 环境
    venv_dir = base_dir / "envs" / "paraformer"
    py_exe = venv_dir / "Scripts" / "python.exe" if sys.platform == "win32" else venv_dir / "bin" / "python"
    if py_exe.exists():
        try:
            ver = subprocess.run(
                [str(py_exe), "--version"], capture_output=True, text=True, timeout=5,
            )
            checks.append(("Paraformer venv", True, f"已创建: {ver.stdout.strip()}", ""))
        except Exception:
            checks.append(("Paraformer venv", True, f"已创建: {py_exe}", ""))
    else:
        checks.append(("Paraformer venv", False, f"未创建: {venv_dir}", "运行 noteforge --setup 或手动: py -3.10 -m venv envs/paraformer"))

    # 9. 输出目录
    output_dir = base_dir / "output"
    if output_dir.exists():
        checks.append(("output 目录", True, f"已存在: {output_dir}", ""))
    else:
        checks.append(("output 目录", None, f"不存在（首次运行时自动创建）", ""))

    # 打印结果
    print("\n" + "=" * 60)
    print("  NoteForge 环境诊断")
    print("=" * 60)

    has_issues = False
    for name, ok, detail, fix in checks:
        if ok is True:
            symbol = "OK"
        elif ok is None:
            symbol = "WARN"
        else:
            symbol = "FAIL"
            has_issues = True
        line = f"  [{symbol}] {name}: {detail}"
        if fix:
            line += f"\n         修复: {fix}"
        print(line)

    print("=" * 60)

    # 统计
    ok_count = sum(1 for _, ok, _, _ in checks if ok is True)
    warn_count = sum(1 for _, ok, _, _ in checks if ok is None)
    fail_count = sum(1 for _, ok, _, _ in checks if ok is False)
    print(f"  结果: {ok_count} OK, {warn_count} WARN, {fail_count} FAIL")

    if fail_count > 0:
        print("  请按修复建议解决 FAIL 项后重新运行 --doctor")
        return 1
    else:
        print("  环境就绪！")
        return 0


def run_health_check(args, base_dir=None):
    """验证所有组件健康状态（--health-check）"""
    return run_doctor(args, base_dir=base_dir)


def run_health_check_asr(args, base_dir=None):
    """仅验证 ASR 组件（--health-check-asr）"""
    if base_dir is None:
        base_dir = _get_base_dir()
    base_dir = Path(base_dir)
    venv_dir = base_dir / "envs" / "paraformer"
    py_exe = venv_dir / "Scripts" / "python.exe" if sys.platform == "win32" else venv_dir / "bin" / "python"

    print("\n" + "=" * 50)
    print("  NoteForge ASR 组件健康检查")
    print("=" * 50)

    issues = 0

    # 1. Paraformer venv
    if py_exe.exists():
        print(f"  [OK] Paraformer Python: {py_exe}")
        # 检查版本
        try:
            ver = subprocess.run(
                [str(py_exe), "--version"], capture_output=True, text=True, timeout=5,
            )
            print(f"       版本: {ver.stdout.strip()}")
        except Exception as e:
            print(f"  [FAIL] 无法获取版本: {e}")
            issues += 1
    else:
        print(f"  [FAIL] Paraformer Python 不存在: {py_exe}")
        print("         修复: 运行 noteforge --setup")
        issues += 1

    # 2. FunASR
    if py_exe.exists():
        try:
            result = subprocess.run(
                [str(py_exe), "-c", "import funasr; print(funasr.__version__)"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                print(f"  [OK] FunASR: {result.stdout.strip()}")
            else:
                print(f"  [FAIL] FunASR 未安装")
                print("         修复: pip install -r requirements-asr.txt --extra-index-url https://download.pytorch.org/whl/cpu")
                issues += 1
        except Exception as e:
            print(f"  [FAIL] FunASR 检查异常: {e}")
            issues += 1

    # 3. torch
    if py_exe.exists():
        try:
            result = subprocess.run(
                [str(py_exe), "-c", "import torch; print(torch.__version__)"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                print(f"  [OK] PyTorch: {result.stdout.strip()}")
            else:
                print(f"  [FAIL] PyTorch 未安装")
                print("         修复: pip install torch --extra-index-url https://download.pytorch.org/whl/cpu")
                issues += 1
        except Exception as e:
            print(f"  [FAIL] PyTorch 检查异常: {e}")
            issues += 1

    # 4. ffmpeg
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        print(f"  [OK] ffmpeg: {ffmpeg_path}")
    else:
        print(f"  [FAIL] ffmpeg 未找到")
        print("         修复: winget install ffmpeg")
        issues += 1

    print("=" * 50)
    if issues:
        print(f"  {issues} 项失败，ASR 功能不可用")
        return 1
    else:
        print("  ASR 组件就绪！")
        return 0
