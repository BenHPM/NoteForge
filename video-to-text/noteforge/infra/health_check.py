# -*- coding: utf-8 -*-
"""
NoteForge 系统健康检查

统一检查各组件可用性：Python 环境、ASR、LLM、飞书、配置文件。

用法:
    from noteforge.infra.health_check import run_health_check
    results = run_health_check()           # 检查全部
    results = run_health_check(['asr'])    # 仅检查 ASR
    # results: {'asr': (True, 'Paraformer 环境正常'), ...}
"""

import os
import sys
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger('noteforge.health_check')

# 支持的检查组件
ALL_COMPONENTS = ('python', 'asr', 'llm', 'feishu', 'config')


def _check_python() -> tuple[bool, str]:
    """检查 Python 环境和关键依赖"""
    # Python 版本
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # tiktoken 可导入性
    try:
        import tiktoken  # noqa: F401
        tiktoken_ok = True
    except ImportError:
        tiktoken_ok = False

    if not tiktoken_ok:
        return False, f"Python {py_version} — tiktoken 不可导入"

    return True, f"Python {py_version} — tiktoken 可用"


def _check_asr() -> tuple[bool, str]:
    """检查 ASR 提供商可用性"""
    # CI 环境使用 MockASR
    ci_env = os.environ.get('CI', '').lower() in ('1', 'true', 'yes')
    noteforge_test = os.environ.get('NOTEFORGE_TEST', '').lower() in ('1', 'true', 'yes')

    if ci_env or noteforge_test:
        from noteforge.sources.asr_provider import MockASR
        provider = MockASR()
        return provider.health_check()

    # 生产环境使用 LocalParaformerASR
    from noteforge.sources.asr_provider import LocalParaformerASR
    provider = LocalParaformerASR()
    return provider.health_check()


def _check_llm() -> tuple[bool, str]:
    """检查 LLM 提供商可用性"""
    try:
        from noteforge.core.llm_providers import create_provider
        from noteforge.infra.file_io import read_file
        import yaml  # noqa: F401
    except ImportError as e:
        return False, f"LLM 模块导入失败: {e}"

    # 尝试加载配置并创建 provider
    try:
        config_path = Path(__file__).parent.parent.parent / "config" / "llm_engine_config.yaml"
        if not config_path.exists():
            return False, "llm_engine_config.yaml 不存在"

        content = read_file(str(config_path))
        import yaml
        config = yaml.safe_load(content)

        provider_config = config.get('provider', {})
        if not provider_config:
            return False, "llm_engine_config.yaml 中无 provider 配置"

        provider = create_provider(provider_config)
        return provider.health_check()
    except Exception as e:
        # 配置加载失败不等于 LLM 不可用，可能是 API key 未设置
        error_msg = str(e)
        if 'api_key' in error_msg.lower() or 'api key' in error_msg.lower():
            return False, f"LLM API key 未配置: {error_msg[:100]}"
        return False, f"LLM 检查异常: {error_msg[:100]}"


def _check_feishu() -> tuple[bool, str]:
    """检查飞书集成可用性"""
    issues = []

    # 1. lark-cli 在 PATH 中
    try:
        result = subprocess.run(
            ['lark-cli', '--version'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            issues.append("lark-cli 执行失败")
    except FileNotFoundError:
        issues.append("lark-cli 不在 PATH 中")
    except subprocess.TimeoutExpired:
        issues.append("lark-cli 响应超时")
    except Exception as e:
        issues.append(f"lark-cli 检查异常: {e}")

    # 2. feishu 模块可导入
    try:
        from noteforge.integration.feishu import FeishuClient  # noqa: F401
    except ImportError as e:
        issues.append(f"feishu 模块导入失败: {e}")

    if issues:
        return False, "; ".join(issues)

    return True, "飞书集成可用 (lark-cli + feishu 模块)"


def _check_config() -> tuple[bool, str]:
    """检查配置文件存在且可解析"""
    config_dir = Path(__file__).parent.parent.parent / "config"
    required_files = {
        'llm_engine_config.yaml': None,
        'note_generation_rules.yaml': None,
    }

    try:
        import yaml
    except ImportError:
        return False, "yaml 模块不可导入"

    issues = []
    for filename in required_files:
        filepath = config_dir / filename
        if not filepath.exists():
            issues.append(f"{filename} 不存在")
            continue

        try:
            from noteforge.infra.file_io import read_file
            content = read_file(str(filepath))
            yaml.safe_load(content)
        except Exception as e:
            issues.append(f"{filename} 解析失败: {e}")

    if issues:
        return False, "; ".join(issues)

    return True, "配置文件完整且可解析"


# 组件检查函数映射（使用模块名延迟查找，支持 unittest.mock.patch）
_CHECK_MAP = {
    'python': '_check_python',
    'asr': '_check_asr',
    'llm': '_check_llm',
    'feishu': '_check_feishu',
    'config': '_check_config',
}


def run_health_check(components: Optional[list] = None) -> dict:
    """运行健康检查

    Args:
        components: 要检查的组件列表（如 ['asr', 'llm']）。
                    None 或空列表表示检查全部。

    Returns:
        {component_name: (is_healthy, diagnostic_message)}
    """
    if not components:
        components = list(ALL_COMPONENTS)

    # 验证组件名
    unknown = [c for c in components if c not in _CHECK_MAP]
    if unknown:
        logger.warning("未知组件: %s（可选: %s）", unknown, list(ALL_COMPONENTS))

    # 延迟查找：通过 globals() 获取函数，使 mock.patch 能生效
    _module_globals = globals()

    results = {}
    for component in components:
        fn_name = _CHECK_MAP.get(component)
        if not fn_name:
            results[component] = (False, f"未知组件: {component}")
            continue

        check_fn = _module_globals.get(fn_name)
        if not check_fn:
            results[component] = (False, f"未知组件: {component}")
            continue

        try:
            results[component] = check_fn()
        except Exception as e:
            logger.error("健康检查异常 [%s]: %s", component, e)
            results[component] = (False, f"检查异常: {e}")

    return results
