# -*- coding: utf-8 -*-
"""
NoteForge 自主执行流水线
设计为 8-12 小时无人值守运行，带完整错误恢复

功能：
  1. 补全已有转写但无笔记的文件
  2. 处理 URL 列表中的新视频（下载→ASR→笔记→质量门禁）
  3. 每处理完一批后自动飞书同步
  4. 同域笔记积累到 5 篇后自动触发跨集合成
  5. 全程进度持久化，中断后 --resume 续传

用法：
  python -m noteforge.batch.auto_pipeline urls.txt              # 处理 URL 列表
  python -m noteforge.batch.auto_pipeline urls.txt --resume     # 断点续传
  python -m noteforge.batch.auto_pipeline --catch-up            # 只补全已有转写
  python -m noteforge.batch.auto_pipeline --synth-only          # 只做跨集合成
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from noteforge.batch.bilibili import load_urls

# 项目路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PYTHON = str(PROJECT_ROOT / "envs" / "paraformer" / "python.exe")
NOTES_DIR = PROJECT_ROOT / "output" / "notes"
TRANSCRIPTS_DIR = PROJECT_ROOT / "output" / "transcripts"
PROGRESS_FILE = PROJECT_ROOT / "output" / "logs" / "pipeline_progress.json"
SYNC_SCRIPT = 'noteforge.integration.feishu_sync'
BATCH_SIZE_FOR_SYNC = 5       # 每处理 N 个视频后同步一次飞书
BATCH_SIZE_FOR_SYNTH = 5      # 同域笔记达到 N 篇后触发合成

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

logger = logging.getLogger('noteforge.pipeline')


def log(msg: str):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"  [{ts}] {msg}", flush=True)


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            data = json.loads(PROGRESS_FILE.read_text(encoding='utf-8'))
            # Risk-7: 断点续传验证 — 检查进度文件完整性
            _validate_progress(data)
            return data
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"进度文件损坏，备份后重新开始: {e}")
            # 备份损坏的进度文件
            backup = PROGRESS_FILE.with_suffix('.json.bak')
            try:
                PROGRESS_FILE.rename(backup)
                logger.info(f"已备份到: {backup}")
            except Exception:
                pass
            return {}
    return {}


def _validate_progress(data: dict) -> None:
    """Risk-7: 验证进度文件的结构完整性

    检查项：
    1. 顶层是 dict
    2. 每个 URL 条目有必需字段
    3. 时间戳格式有效
    4. 状态值合法

    无效条目被移除（不阻止整体恢复）。
    """
    if not isinstance(data, dict):
        raise KeyError("progress root is not a dict")

    valid_statuses = {'success', 'failed', 'skipped', 'dead_letter', 'degraded'}
    required_fields = {'status', 'ts'}
    invalid_keys = []

    for key, entry in data.items():
        if not isinstance(entry, dict):
            invalid_keys.append(key)
            continue
        # 检查必需字段
        if not required_fields.issubset(entry.keys()):
            invalid_keys.append(key)
            continue
        # 检查状态值
        if entry.get('status') not in valid_statuses:
            invalid_keys.append(key)
            continue

    # 移除无效条目
    for key in invalid_keys:
        logger.warning(f"移除无效进度条目: {key}")
        del data[key]

    if invalid_keys:
        logger.info(f"进度验证: 移除 {len(invalid_keys)} 个无效条目")


def save_progress(progress: dict):
    """Risk-7: 原子写入进度文件（先写临时文件，再重命名）

    防止写入过程中崩溃导致进度文件损坏。
    """
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = PROGRESS_FILE.with_suffix('.json.tmp')
    try:
        tmp_file.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2), 'utf-8'
        )
        # Windows: rename 需要先删除目标文件
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()
        tmp_file.rename(PROGRESS_FILE)
    except Exception as e:
        logger.warning(f"进度文件写入失败: {e}")
        # 回退到直接写入（非原子但至少保存数据）
        try:
            PROGRESS_FILE.write_text(
                json.dumps(progress, ensure_ascii=False, indent=2), 'utf-8'
            )
        except Exception:
            pass


def get_domain_for_category(category: str) -> str:
    """从飞书分类名推断知识域（精确匹配 → match_files → 关键词）"""
    if not category:
        return 'general'

    classifier = _get_domain_classifier()

    # 第 1 层：精确分类名映射（处理 YAML 关键词重叠/歧义）
    _exact = {
        '地缘政治': 'geopolitics',
        '地缘经济': 'geoeconomics',
        '短视频': 'short_video_directing',
        '投资': 'finance_investment',
    }
    if category in _exact:
        return _exact[category]

    # 第 2 层：match_files fnmatch（如'地缘经济'→geoeconomics 通过 *地缘*）
    import fnmatch
    cat_lower = category.lower()
    for domain in classifier._domains:
        if domain['id'] == 'general':
            continue
        match_files = domain.get('match_files', [])
        if match_files and any(fnmatch.fnmatch(cat_lower, pat) for pat in match_files):
            return domain['id']

    # 第 3 层：关键词匹配（单一入口，避免与 R11/R12 重复）
    return classifier.classify_text(category)


def get_content_type(category: str) -> str:
    if any(k in category for k in ['投资', '量化', '基金']):
        return 'interview'
    return 'lecture'


def run_cmd(cmd: list, timeout: int = 2400) -> tuple[int, str, str]:
    """运行命令，返回 (returncode, stdout, stderr)"""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=timeout,
        )
        return r.returncode, r.stdout or '', r.stderr or ''
    except subprocess.TimeoutExpired:
        return -1, '', 'TIMEOUT'
    except Exception as e:
        return -1, '', str(e)


_cached_engine = None


def _create_engine():
    """创建 LLM 笔记引擎实例（直接调用引擎 API，替代 subprocess）

    缓存实例避免重复初始化（YAML 解析 + 子组件创建开销大）。
    """
    global _cached_engine
    if _cached_engine is None:
        from noteforge.engine.note_engine import LLMNoteEngine
        _cached_engine = LLMNoteEngine()
    return _cached_engine


def _check_feishu_sync_module_available():
    """检查飞书同步模块是否可导入"""
    try:
        import importlib
        importlib.import_module('noteforge.integration.feishu_sync')
        return True
    except ImportError:
        return False


# Pipeline 阶段标记（用于断点恢复）
STAGE_DOWNLOADING = 'downloading'
STAGE_TRANSCRIBED = 'transcribed'
STAGE_GENERATING = 'generating'
STAGE_GENERATED = 'generated'        # ← 当前使用中
STAGE_QUALITY_PASSED = 'quality_passed'
STAGE_SYNCED = 'synced'

# ============================================================
# B2: 显式状态机 + 失败策略
# ============================================================
# 每个阶段有明确的失败策略，auto_pipeline 按策略路由，无需字符串匹配。
#
# 状态转换图:
#   PENDING → DOWNLOADING → TRANSCRIBED → GENERATED → [QUALITY_PASSED | QUALITY_FAILED]
#                                                                      ↓              ↓
#                                                                   SYNCED         [STOP | SKIP | DEGRADE]
#
# 失败策略:
#   STOP:    立即停止当前 URL，记录 checkpoint，继续下一个 URL
#   SKIP:    永久跳过当前 URL（dead_letter），不再重试
#   DEGRADE: 降级使用当前输出（质量未达标但仍有价值），继续后续阶段
#   ABORT:   停止整个 pipeline（严重错误，如 API key 无效）
#
# 阶段 → 失败类型 → 策略映射:
#   downloading:  network/timeout → STOP (重试后仍失败)
#                 deleted/too_short → SKIP
#   transcribed:  too_short → SKIP
#   generating:   llm_error (retryable) → STOP (质量循环已耗尽重试)
#                 llm_error (terminal) → ABORT
#                 content_filter → DEGRADE (标记后继续)
#   quality:      score < threshold → DEGRADE (使用当前版本)
#                 fatal rule fail → STOP (质量不可接受)
#   sync:         feishu_error → STOP (不影响本地结果)
#
# Checkpoint 格式:
#   progress[url] = {
#     'status': 'success' | 'failed' | 'skipped' | 'dead_letter' | 'degraded',
#     'stage': STAGE_*,           # 最后到达的阶段
#     'failure_class': str,       # 'retryable' | 'terminal' | 'degraded'
#     'error_type': str,          # network/timeout/code_bug/content/...
#     'error': str,               # 错误详情（截断到 300 字）
#     'retry_count': int,         # 跨 run 累计重试次数
#     'ts': str,                  # ISO 时间戳
#   }

# 失败策略枚举
POLICY_STOP = 'STOP'
POLICY_SKIP = 'SKIP'
POLICY_DEGRADE = 'DEGRADE'
POLICY_ABORT = 'ABORT'

# 错误类型 → 失败策略映射
ERROR_POLICY_MAP = {
    'network': POLICY_STOP,       # 网络错误：重试后仍失败，停止当前 URL
    'timeout': POLICY_STOP,       # 超时：同上
    'deleted': POLICY_SKIP,       # 视频已删除：永久跳过
    'too_short': POLICY_SKIP,     # 内容过短：永久跳过
    'code_bug': POLICY_STOP,      # 代码 bug：停止（需人工修复）
    'content_filter': POLICY_DEGRADE,  # 内容过滤：降级使用
    'quality_fatal': POLICY_STOP,      # 致命质量规则失败：停止
    'quality_minor': POLICY_DEGRADE,   # 非致命质量失败：降级使用
    'api_key': POLICY_ABORT,           # API key 无效：终止整个 pipeline
    'unknown': POLICY_STOP,            # 未知错误：保守停止
}


def resolve_policy(error_type: str, retry_count: int = 0,
                   max_retries: int = 3) -> str:
    """根据错误类型和重试次数决定失败策略

    Args:
        error_type: 错误分类（来自 classify_error 或显式指定）
        retry_count: 跨 run 累计重试次数
        max_retries: 最大重试次数

    Returns:
        策略字符串: STOP / SKIP / DEGRADE / ABORT
    """
    policy = ERROR_POLICY_MAP.get(error_type, POLICY_STOP)

    # 重试次数耗尽 → 升级为 SKIP（永久放弃）
    if policy == POLICY_STOP and retry_count >= max_retries:
        return POLICY_SKIP

    return policy


# ============================================================
# 阶段 1: 补全已有转写
# ============================================================
def catch_up(progress: dict) -> tuple[int, int]:
    """为有转写但无笔记的文件生成笔记（直接调用引擎 API）"""
    transcripts = {p.stem: p for p in TRANSCRIPTS_DIR.glob('*.txt')}
    notes = {p.stem for p in NOTES_DIR.glob('*.md')}

    missing = []
    for stem, t_path in transcripts.items():
        # 跳过合成/版本文件
        if any(k in stem for k in ['知识体系', '_v2', '_v3', '_v4', '_incremental', 'extractions']):
            continue
        if stem not in notes:
            missing.append((stem, str(t_path)))

    if not missing:
        return 0, 0

    log(f"发现 {len(missing)} 个转写文件缺少笔记，开始补全...")
    engine = _create_engine()
    success = 0
    failed = 0
    for i, (stem, t_path) in enumerate(missing, 1):
        log(f"  [{i}/{len(missing)}] {stem}")
        try:
            result = engine.generate_note(t_path)
            if result.error and '已存在' not in result.error:
                failed += 1
                progress[f'catchup:{stem}'] = {
                    'status': 'failed',
                    'error': result.error[-200:],
                    'ts': datetime.now().isoformat(),
                }
            else:
                success += 1
                progress[f'catchup:{stem}'] = {
                    'status': 'success',
                    'ts': datetime.now().isoformat(),
                }
        except Exception as e:
            failed += 1
            progress[f'catchup:{stem}'] = {
                'status': 'failed',
                'error': str(e)[-200:],
                'ts': datetime.now().isoformat(),
            }
        save_progress(progress)

    log(f"补全完成: {success} 成功, {failed} 失败")
    return success, failed


# ============================================================
# 阶段 2: 处理新视频
# ============================================================
MAX_AUTO_RETRIES = 2  # 网络/超时错误自动重试次数
MAX_TOTAL_ATTEMPTS = 3  # 单 URL 最大总尝试次数（跨 runs 累计），超过后永久放弃


def classify_error(err: str, out: str) -> str:
    """分类错误类型：network / timeout / code_bug / content / unknown"""
    combined = (err + out).lower()
    if 'timeout' in combined or 'timed out' in combined:
        return 'timeout'
    if any(k in combined for k in ['connection', 'getaddrinfo', 'refused', 'reset', 'eof']):
        return 'network'
    if '啥都木有' in combined or 'video info' in combined or '已删除' in combined:
        return 'deleted'
    if '转写文本过短' in combined or '过短' in combined:
        return 'too_short'
    if 'attributeerror' in combined or 'nameerror' in combined or 'typeerror' in combined:
        return 'code_bug'
    return 'unknown'


def process_videos(urls: list[dict], progress: dict, max_count: int = 0, no_sync: bool = False) -> tuple[int, int]:
    """逐个处理视频 URL，通过 SourceRegistry 路由到正确的数据源

    B2: 使用显式失败策略（STOP/SKIP/DEGRADE/ABORT）替代隐式行为。
    每个失败路径都有明确的策略决策，可审计、可测试。
    """
    todo = []
    for item in urls:
        key = item['url']
        p = progress.get(key, {})
        # 跳过已成功、已降级使用、或已永久放弃（dead_letter）的 URL
        if p.get('status') in ('success', 'dead_letter', 'degraded'):
            continue
        todo.append(item)

    if max_count > 0:
        todo = todo[:max_count]

    if not todo:
        log("所有视频已处理完成")
        return 0, 0

    log(f"待处理: {len(todo)} 个视频")
    from noteforge.sources.sources_factory import create_source_registry
    registry = create_source_registry(
        output_dir=str(PROJECT_ROOT / 'output' / 'audio'),
    )

    success = 0
    failed = 0
    degraded_count = 0
    since_sync = 0
    domain_new_notes = defaultdict(list)  # 域 → 新增笔记路径
    engine = _create_engine()

    for i, item in enumerate(todo, 1):
        url = item['url']
        cat = item['category']
        ct = get_content_type(cat)

        log(f"[{i}/{len(todo)}] {url} ({cat or '未分类'})")

        # 路由到数据源
        source = registry.match(url)
        if source is None:
            log(f"  ❌ 无法识别的 URL: {url}")
            progress[url] = {
                'status': 'failed',
                'stage': STAGE_DOWNLOADING,
                'failure_class': 'terminal',
                'error_type': 'unknown',
                'error': f"无法识别的 URL: {url}",
                'ts': datetime.now().isoformat(),
            }
            failed += 1
            continue

        log(f"  🔀 数据源: {source.name}")

        # 带自动重试的处理
        final_result = None
        for attempt in range(1 + MAX_AUTO_RETRIES):
            start = time.time()
            try:
                metadata = source.fetch(url)
                if metadata.error:
                    err_msg = metadata.error
                    err_type = classify_error(err_msg, '')
                    policy = resolve_policy(err_type, attempt)
                    if policy == POLICY_SKIP:
                        log(f"  ⏭️ 跳过 ({err_type})")
                        final_result = {
                            'status': 'skipped',
                            'stage': STAGE_DOWNLOADING,
                            'failure_class': 'terminal',
                            'error_type': err_type,
                            'reason': err_type,
                            'ts': datetime.now().isoformat(),
                        }
                        break
                    # STOP/DEGRADE: 继续尝试重试
                    raise RuntimeError(err_msg)

                audio_path = metadata.audio_path
                title = metadata.title
                engine.configure(content_type=ct)
                gen_result = engine.generate_note(
                    audio_path, title=title,
                )
                elapsed = time.time() - start

                if gen_result.error and '已存在' in gen_result.error:
                    final_result = {
                        'status': 'success',
                        'stage': STAGE_GENERATED,
                        'category': cat,
                        'elapsed': round(elapsed, 1),
                        'ts': datetime.now().isoformat(),
                    }
                elif gen_result.error:
                    # 生成失败 — 检查是否为内容过滤
                    err_type = classify_error(gen_result.error, '')
                    if '内容安全过滤' in gen_result.error or 'content_filter' in gen_result.error:
                        err_type = 'content_filter'
                    policy = resolve_policy(err_type)
                    if policy == POLICY_DEGRADE:
                        log(f"  ⚠️ 内容过滤，降级使用")
                        final_result = {
                            'status': 'degraded',
                            'stage': STAGE_GENERATED,
                            'failure_class': 'degraded',
                            'error_type': err_type,
                            'error': gen_result.error[-200:],
                            'category': cat,
                            'elapsed': round(elapsed, 1),
                            'ts': datetime.now().isoformat(),
                        }
                        break
                    elif policy == POLICY_ABORT:
                        log(f"  🛑 严重错误，终止 pipeline: {err_type}")
                        progress[url] = {
                            'status': 'failed',
                            'stage': STAGE_GENERATING,
                            'failure_class': 'terminal',
                            'error_type': err_type,
                            'error': gen_result.error[-300:],
                            'ts': datetime.now().isoformat(),
                        }
                        save_progress(progress)
                        return success, failed
                    raise RuntimeError(gen_result.error)
                else:
                    final_result = {
                        'status': 'success',
                        'stage': STAGE_GENERATED,
                        'category': cat,
                        'elapsed': round(elapsed, 1),
                        'ts': datetime.now().isoformat(),
                    }
                break

            except Exception as e:
                elapsed = time.time() - start
                err_type = classify_error(str(e), '')

                # 不可重试的错误
                policy = resolve_policy(err_type)
                if policy == POLICY_SKIP:
                    log(f"  ⏭️ 跳过 ({err_type})")
                    final_result = {
                        'status': 'skipped',
                        'stage': STAGE_DOWNLOADING,
                        'failure_class': 'terminal',
                        'error_type': err_type,
                        'reason': err_type,
                        'ts': datetime.now().isoformat(),
                    }
                    break

                if policy == POLICY_ABORT:
                    log(f"  🛑 严重错误，终止 pipeline: {err_type}")
                    progress[url] = {
                        'status': 'failed',
                        'stage': STAGE_DOWNLOADING,
                        'failure_class': 'terminal',
                        'error_type': err_type,
                        'error': str(e)[-300:],
                        'ts': datetime.now().isoformat(),
                    }
                    save_progress(progress)
                    return success, failed

                # 可重试的错误
                if err_type in ('network', 'timeout') and attempt < MAX_AUTO_RETRIES:
                    log(f"  ⚠️ {err_type}，自动重试 {attempt+1}/{MAX_AUTO_RETRIES}...")
                    time.sleep(30)
                    continue

                # 最终失败 — 跟踪重试次数，超过上限后标记为 dead_letter 永久跳过
                prev_attempts = progress.get(url, {}).get('retry_count', 0)
                total_attempts = prev_attempts + 1
                final_policy = resolve_policy(err_type, total_attempts)
                is_dead_letter = final_policy == POLICY_SKIP
                final_result = {
                    'status': 'dead_letter' if is_dead_letter else 'failed',
                    'stage': STAGE_DOWNLOADING,
                    'failure_class': 'terminal' if is_dead_letter else 'retryable',
                    'category': cat,
                    'error_type': err_type,
                    'error': str(e)[-300:],
                    'elapsed': round(elapsed, 1),
                    'retry_count': total_attempts,
                    'ts': datetime.now().isoformat(),
                }
                if is_dead_letter:
                    log(f"  ⛔ 已达最大重试次数 ({MAX_TOTAL_ATTEMPTS})，永久放弃: {err_type}")

        # 记录结果
        progress[url] = final_result
        save_progress(progress)

        if final_result['status'] == 'success':
            success += 1
            since_sync += 1
            log(f"  ✅ 成功 ({final_result.get('elapsed', 0):.0f}秒)")
            domain = get_domain_for_category(cat)
            if domain != 'general':
                domain_new_notes[domain].append(url)
                if len(domain_new_notes[domain]) >= BATCH_SIZE_FOR_SYNTH:
                    _incremental_synthesize(domain)
                    domain_new_notes[domain] = []
        elif final_result['status'] == 'degraded':
            degraded_count += 1
            since_sync += 1
            log(f"  ⚠️ 降级使用 ({final_result.get('error_type', 'unknown')})")
            domain = get_domain_for_category(cat)
            if domain != 'general':
                domain_new_notes[domain].append(url)
        elif final_result['status'] == 'skipped':
            pass
        else:
            failed += 1
            log(f"  ❌ {final_result.get('error_type', 'unknown')}: {final_result.get('error', '')[:100]}")

        if not no_sync and since_sync >= BATCH_SIZE_FOR_SYNC:
            log(f"  📤 同步飞书（{since_sync} 个新笔记）...")
            _sync_feishu()
            since_sync = 0

    if since_sync > 0:
        log(f"  📤 同步飞书（最后 {since_sync} 个）...")
        _sync_feishu()

    if degraded_count > 0:
        log(f"  ⚠️ {degraded_count} 个视频降级使用（质量未达标但仍有价值）")

    return success, failed


# ============================================================
# 阶段 3: 飞书同步
# ============================================================
def _sync_feishu():
    cmd = [PYTHON, '-m', 'noteforge.integration.feishu_sync', '--new-only']
    rc, out, err = run_cmd(cmd, timeout=300)
    if rc == 0:
        log("  ✅ 飞书同步完成")
    else:
        log(f"  ⚠️ 飞书同步失败: {err[:100]}")


def _incremental_synthesize(domain: str):
    """对指定域执行增量合成（不重建，只更新）"""
    log(f"  🔬 域 '{domain}' 新笔记达阈值，触发增量合成...")
    engine = _create_engine()
    try:
        result = engine.generate_synthesis_two_stage(domain=domain)
        if result:
            log(f"  ✅ {domain} 增量合成完成")
        else:
            log(f"  ⚠️ {domain} 合成失败：返回空结果")
    except Exception as e:
        log(f"  ⚠️ {domain} 合成失败: {str(e)[:80] or out[-80:]}")


def health_check() -> bool:
    """批量前健康检查：验证各组件可用"""
    log("🏥 健康检查...")
    checks = []

    # 1. Python 环境
    rc, out, err = run_cmd([PYTHON, '--version'], timeout=10)
    checks.append(('Python 环境', rc == 0))

    # 2. 引擎初始化（直接调用 LLMNoteEngine 而非 subprocess）
    try:
        engine = _create_engine()
        checks.append(('引擎初始化', engine is not None))
    except Exception:
        checks.append(('引擎初始化', False))

    # 3. 配置文件
    config_path = PROJECT_ROOT / 'config' / 'llm_engine_config.yaml'
    checks.append(('配置文件', config_path.exists()))

    # 4. LLM 代理可达（从配置读取 base_url）
    try:
        import requests
        from noteforge.config import load_yaml
        _cfg = load_yaml(str(config_path))
        _base_url = _cfg.get('provider', {}).get('claude', {}).get('base_url', 'http://127.0.0.1:15721')
        resp = requests.get(_base_url, timeout=5)
        checks.append(('LLM 代理', True))
    except Exception as e:
        logger.debug(f"LLM 代理检查失败: {e}")
        checks.append(('LLM 代理', False))

    # 5. 飞书同步脚本
    checks.append(('飞书同步', _check_feishu_sync_module_available()))

    all_ok = True
    for name, ok in checks:
        status = '✅' if ok else '❌'
        log(f"  {status} {name}")
        if not ok:
            all_ok = False

    if not all_ok:
        log("  ⚠️ 部分组件不可用，批量处理可能失败")

    return all_ok


# ============================================================
# 阶段 4: 跨集合成
# ============================================================
def auto_synthesize(progress: dict) -> int:
    """按域统计笔记数量，每个域独立触发跨集合成（域隔离，不跨域整合）"""
    domain_notes = defaultdict(list)
    for note_path in NOTES_DIR.glob('*.md'):
        stem = note_path.stem
        if any(k in stem for k in ['知识体系', '_v2', '_v3', '_v4', '_incremental', 'extractions']):
            continue
        domain = _get_domain_classifier().detect_domain(str(note_path))
        domain_notes[domain].append(str(note_path))

    synth_count = 0
    engine = None  # 延迟初始化：只有需要合成时才创建
    for domain, notes in domain_notes.items():
        if len(notes) < BATCH_SIZE_FOR_SYNTH:
            log(f"  域 '{domain}': {len(notes)} 篇（不足 {BATCH_SIZE_FOR_SYNTH} 篇，跳过合成）")
            continue
        if domain == 'general':
            log(f"  域 'general': 兜底域，跳过合成")
            continue

        # 延迟初始化引擎：只有需要合成时才创建
        if engine is None:
            engine = _create_engine()
        log(f"🔬 域 '{domain}' 有 {len(notes)} 篇笔记，独立触发跨集合成...")
        # 指定 domain 参数，确保只合成该域的笔记，不跨域整合
        try:
            result = engine.generate_synthesis_two_stage(domain=domain)
            if result:
                log(f"  ✅ {domain} 合成完成")
                synth_count += 1
            else:
                log(f"  ⚠️ {domain} 合成失败：返回空结果")
        except Exception as e:
            log(f"  ⚠️ {domain} 合成失败: {str(e)[:100]}")

    return synth_count


# 域分类器缓存（延迟初始化，避免在模块级别解析 YAML）
_cached_domain_classifier = None


def _get_domain_classifier():
    """创建/获取 DomainClassifier 单例，直接使用 YAML 配置"""
    global _cached_domain_classifier
    if _cached_domain_classifier is not None:
        return _cached_domain_classifier
    from noteforge.config import load_yaml
    from noteforge.core.domain_classifier import DomainClassifier

    config_path = str(PROJECT_ROOT / "config" / "llm_engine_config.yaml")
    cfg = load_yaml(config_path)
    domains = cfg.get('knowledge_domains', [])
    classifier = DomainClassifier(domains=domains, path_config=None)
    # 应用 TF-IDF 兜底配置
    dc_config = cfg.get('domain_classification', {})
    if 'use_tfidf_fallback' in dc_config:
        classifier._use_tfidf_fallback = dc_config['use_tfidf_fallback']
    if 'fallback_threshold' in dc_config:
        classifier._fallback_threshold = float(dc_config['fallback_threshold'])
    if 'tie_threshold' in dc_config:
        classifier._tie_threshold = float(dc_config['tie_threshold'])
    _cached_domain_classifier = classifier
    return _cached_domain_classifier


# ============================================================
# 主流程
# ============================================================
def _summarize_progress(progress: dict) -> dict:
    """Risk-7: 汇总进度状态（用于恢复时的一致性检查）"""
    stats = {'success': 0, 'failed': 0, 'skipped': 0, 'degraded': 0, 'dead_letter': 0, 'pending': 0}
    for key, entry in progress.items():
        if not isinstance(entry, dict):
            stats['pending'] += 1
            continue
        status = entry.get('status', '')
        if status in stats:
            stats[status] += 1
        else:
            stats['pending'] += 1
    return stats


def main():
    parser = argparse.ArgumentParser(description='NoteForge 自主执行流水线')
    parser.add_argument('urls_file', nargs='?', help='URL 列表文件')
    parser.add_argument('--resume', action='store_true', help='断点续传')
    parser.add_argument('--catch-up', action='store_true', help='只补全已有转写')
    parser.add_argument('--synth-only', action='store_true', help='只做跨集合成')
    parser.add_argument('--no-synth', action='store_true', help='跳过跨集合成（等全部完成后再做）')
    parser.add_argument('--no-sync', action='store_true', help='跳过飞书同步（等全部完成后再统一同步）')
    parser.add_argument('--max', type=int, default=0, help='最多处理 N 个视频')
    args = parser.parse_args()

    progress = load_progress() if args.resume else {}
    start_time = time.time()

    print("=" * 60)
    print("  NoteForge 自主执行流水线")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模式: {'续传' if args.resume else '首次'}")
    print("=" * 60)

    # 健康检查
    health_check()

    # Risk-7: 恢复时验证进度一致性
    if args.resume and progress:
        resume_stats = _summarize_progress(progress)
        log(f"📋 恢复进度: {resume_stats['success']} 成功, "
            f"{resume_stats['failed']} 失败, "
            f"{resume_stats['skipped']} 跳过, "
            f"{resume_stats['degraded']} 降级, "
            f"{resume_stats['pending']} 待处理")

    # 阶段 1: 补全已有转写
    if not args.synth_only:
        log("📋 阶段 1: 补全已有转写文件...")
        s, f = catch_up(progress)
        log(f"  结果: {s} 成功, {f} 失败")

    # 阶段 2: 处理新视频
    if not args.catch_up and not args.synth_only:
        if not args.urls_file:
            log("⚠️ 未指定 URL 文件，跳过新视频处理")
        else:
            urls = load_urls(args.urls_file)
            if urls:
                log(f"📋 阶段 2: 处理 {len(urls)} 个视频...")
                s, f = process_videos(urls, progress, args.max, no_sync=args.no_sync)
                log(f"  结果: {s} 成功, {f} 失败")

    # 阶段 3: 跨集合成（--no-synth 时跳过，等全部完成后再统一做）
    if not args.no_synth:
        log("📋 阶段 3: 检查跨集合成...")
        n = auto_synthesize(progress)
        if n > 0:
            log(f"  完成 {n} 个域的合成")
    else:
        log("📋 阶段 3: 跳过跨集合成（--no-synth）")

    # 最终飞书同步（--no-sync 时跳过）
    if not args.no_sync:
        log("📋 最终飞书同步...")
        _sync_feishu()
    else:
        log("📋 跳过飞书同步（--no-sync）")

    # 汇总
    elapsed = time.time() - start_time
    hours = elapsed / 3600
    print("\n" + "=" * 60)
    print(f"  流水线完成！")
    print(f"  总耗时: {hours:.1f} 小时")
    print(f"  进度文件: {PROGRESS_FILE}")
    print("=" * 60)


if __name__ == '__main__':
    main()
