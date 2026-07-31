# -*- coding: utf-8 -*-
"""清理临时文件命令"""
import os
import time
import shutil
from pathlib import Path
from typing import List, Tuple


def _get_base_dir() -> Path:
    """获取 video-to-text 根目录"""
    return Path(__file__).parent.parent.parent.parent


def _clean_old_logs(logs_dir: Path, max_age_days: int = 30) -> List[Path]:
    """删除超过 max_age_days 天的日志文件"""
    removed = []
    if not logs_dir.exists():
        return removed
    cutoff = time.time() - max_age_days * 86400
    for f in logs_dir.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                removed.append(f)
            except OSError:
                pass
    return removed


def _clean_directory(dir_path: Path) -> List[Path]:
    """删除目录内所有文件和子目录，保留目录本身"""
    removed = []
    if not dir_path.exists():
        return removed
    for item in dir_path.iterdir():
        try:
            if item.is_file():
                item.unlink()
                removed.append(item)
            elif item.is_dir():
                shutil.rmtree(item)
                removed.append(item)
        except OSError:
            pass
    return removed


def run_cleanup(args) -> int:
    """清理临时文件

    Flags (from args):
      cleanup_logs       -- 清理旧日志（>30 天）
      cleanup_temp       -- 清理 temp/ 目录
      cleanup_extractions -- 清理 notes/extractions/ 缓存
      cleanup_traces     -- 清理 output/logs/traces/
      cleanup_all        -- 清理所有
    """
    base_dir = _get_base_dir()
    output_dir = base_dir / "output"

    do_logs = getattr(args, 'cleanup_logs', False)
    do_temp = getattr(args, 'cleanup_temp', False)
    do_extractions = getattr(args, 'cleanup_extractions', False)
    do_traces = getattr(args, 'cleanup_traces', False)
    do_all = getattr(args, 'cleanup_all', False)

    # --cleanup implies --cleanup-all for backward compat
    if getattr(args, 'cleanup', False) and not any([do_logs, do_temp, do_extractions, do_traces]):
        do_all = True

    if do_all:
        do_logs = do_temp = do_extractions = do_traces = True

    if not any([do_logs, do_temp, do_extractions, do_traces]):
        print("[INFO] 未指定清理目标。使用 --cleanup-all 清理所有，或指定 --cleanup-logs / --cleanup-temp / --cleanup-extractions / --cleanup-traces")
        return 1

    print("\n" + "=" * 50)
    print("  NoteForge 临时文件清理")
    print("=" * 50)

    total_removed = 0

    if do_logs:
        logs_dir = output_dir / "logs"
        removed = _clean_old_logs(logs_dir, max_age_days=30)
        total_removed += len(removed)
        if removed:
            print(f"  [日志] 清理 {len(removed)} 个旧日志文件（>30 天）")
            for f in removed[:5]:
                print(f"         - {f.name}")
            if len(removed) > 5:
                print(f"         ... 及其他 {len(removed) - 5} 个")
        else:
            print("  [日志] 无旧日志需要清理")

    if do_temp:
        temp_dir = output_dir / "temp"
        removed = _clean_directory(temp_dir)
        total_removed += len(removed)
        if removed:
            print(f"  [临时] 清理 temp/ 目录: {len(removed)} 项")
        else:
            print("  [临时] temp/ 目录为空或不存在")

    if do_extractions:
        extractions_dir = output_dir / "notes" / "extractions"
        removed = _clean_directory(extractions_dir)
        total_removed += len(removed)
        if removed:
            print(f"  [提取] 清理 notes/extractions/ 缓存: {len(removed)} 项")
        else:
            print("  [提取] notes/extractions/ 为空或不存在")

    if do_traces:
        traces_dir = output_dir / "logs" / "traces"
        removed = _clean_directory(traces_dir)
        total_removed += len(removed)
        if removed:
            print(f"  [追踪] 清理 output/logs/traces/: {len(removed)} 项")
        else:
            print("  [追踪] output/logs/traces/ 为空或不存在")

    print("=" * 50)
    print(f"  共清理 {total_removed} 项")
    return 0


def run_provider_status(args) -> int:
    """显示 LLM Provider 状态信息"""
    from noteforge.config import NoteForgeConfig
    from noteforge.core.llm_providers import create_provider, LLMError

    base_dir = _get_base_dir()
    config_path = str(base_dir / "config" / "llm_engine_config.yaml")

    try:
        config_mgr = NoteForgeConfig(config_path=config_path, base_dir=base_dir)
    except Exception as e:
        print(f"[ERROR] 无法加载配置: {e}")
        return 1

    provider_cfg = dict(config_mgr.raw.get('provider', {}))
    provider_type = provider_cfg.get('type', 'claude')
    provider_section = provider_cfg.get(provider_type, {})

    print("\n" + "=" * 50)
    print("  NoteForge LLM Provider 状态")
    print("=" * 50)

    # Provider type and model
    model = provider_section.get('model', 'unknown')
    print(f"  提供商类型: {provider_type}")
    print(f"  模型:       {model}")

    # Base URL
    default_urls = {
        'claude': 'https://api.anthropic.com',
        'openai': 'https://api.openai.com/v1',
        'local': 'http://localhost:11434/v1',
    }
    base_url = provider_section.get('base_url', default_urls.get(provider_type, 'unknown'))
    print(f"  Base URL:   {base_url}")

    # API key configured
    import os
    api_key_env = provider_section.get('api_key_env', {
        'claude': 'ANTHROPIC_API_KEY',
        'openai': 'OPENAI_API_KEY',
        'local': '',
    }.get(provider_type, ''))
    config_key = provider_section.get('api_key', '')
    has_config_key = bool(config_key and config_key not in ('PROXY_MANAGED', 'PLACEHOLDER', ''))
    has_env_key = bool(os.environ.get(api_key_env, '')) if api_key_env else False
    is_proxy = base_url != default_urls.get(provider_type) and not has_config_key and not has_env_key

    if has_config_key or has_env_key:
        print(f"  API Key:    已配置 ({'配置文件' if has_config_key else '环境变量 ' + api_key_env})")
    elif is_proxy:
        print(f"  API Key:    代理托管（无需本地配置）")
    else:
        print(f"  API Key:    未配置")

    # Try to create provider and get usage stats
    try:
        provider_cfg_with_retry = dict(provider_cfg)
        provider_cfg_with_retry['api_retry'] = config_mgr.raw.get('api_retry', {})
        provider = create_provider(provider_cfg_with_retry)

        total = provider.get_total_usage()
        if total.get('calls', 0) > 0:
            print(f"\n  --- 使用统计 ---")
            print(f"  总调用次数:   {total.get('calls', 0)}")
            print(f"  总输入 tokens: {total.get('input_tokens', 0):,}")
            print(f"  总输出 tokens: {total.get('output_tokens', 0):,}")

            # Cache stats (Claude only)
            if provider_type == 'claude' and hasattr(provider, '_total_cache_creation'):
                cache_creation = getattr(provider, '_total_cache_creation', 0)
                cache_read = getattr(provider, '_total_cache_read', 0)
                if cache_creation or cache_read:
                    print(f"\n  --- Prompt Caching 统计 ---")
                    print(f"  缓存创建 tokens: {cache_creation:,}")
                    print(f"  缓存读取 tokens: {cache_read:,}")
        else:
            print(f"\n  使用统计: 尚无调用记录（当前会话）")

    except LLMError as e:
        print(f"\n  [WARN] Provider 初始化失败: {e}")
    except Exception as e:
        print(f"\n  [WARN] 无法获取使用统计: {e}")

    print("=" * 50)
    return 0
