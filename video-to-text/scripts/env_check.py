"""
env_check.py — NoteForge 运行环境检测

在脚本入口处 import 此模块，自动检测是否使用正确的 Python 环境。
检测失败时打印提示并退出。

用法（在脚本顶部）:
    import env_check  # noqa: F401 — 必须在其他 import 之前
"""

import sys
from pathlib import Path


def _check_env():
    """检测关键依赖是否可用。"""
    missing = []

    # tiktoken: 笔记引擎必需
    try:
        import tiktoken
    except ImportError:
        missing.append("tiktoken")

    # 如果有缺失，给出修复建议
    if missing:
        # 判断是否在 Paraformer 环境中
        exe = Path(sys.executable)
        is_paraformer = "paraformer" in str(exe).lower()

        print(f"\033[31m[ERROR]\033[0m 缺少依赖: {', '.join(missing)}")
        print()

        if is_paraformer:
            print("  你已在 Paraformer 环境中，但依赖仍缺失。")
            print("  请运行: pip install " + " ".join(missing))
        else:
            print(f"  当前 Python: {sys.executable}")
            print(f"  应使用: ...\\envs\\paraformer\\python.exe")
            print()
            print("  正确的调用方式:")
            paraformer_py = Path(__file__).parent.parent / "envs" / "paraformer" / "python.exe"
            print(f'    "{paraformer_py}" scripts/{Path(sys.argv[0]).name} ...')
            print()
            print("  或使用 noteforge.bat 菜单（自动使用正确环境）")

        sys.exit(1)


# 自动检测（import 时立即执行）
_check_env()
