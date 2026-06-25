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
  python auto_pipeline.py urls.txt              # 处理 URL 列表
  python auto_pipeline.py urls.txt --resume     # 断点续传
  python auto_pipeline.py --catch-up            # 只补全已有转写
  python auto_pipeline.py --synth-only          # 只做跨集合成
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# 从 batch_bilibili 导入共享的 URL 加载函数
from batch_bilibili import load_urls

# 项目路径
PROJECT_ROOT = Path(__file__).resolve().parent
ENGINE = str(PROJECT_ROOT / "scripts" / "llm_note_engine.py")
PYTHON = str(PROJECT_ROOT / "envs" / "paraformer" / "python.exe")
NOTES_DIR = PROJECT_ROOT / "output" / "notes"
TRANSCRIPTS_DIR = PROJECT_ROOT / "output" / "transcripts"
PROGRESS_FILE = PROJECT_ROOT / "output" / "logs" / "pipeline_progress.json"
SYNC_SCRIPT = str(PROJECT_ROOT.parent / "scripts" / "feishu_sync.py")
BATCH_SIZE_FOR_SYNC = 5       # 每处理 N 个视频后同步一次飞书
BATCH_SIZE_FOR_SYNTH = 5      # 同域笔记达到 N 篇后触发合成
SYNTH_DONE_FLAG = PROJECT_ROOT / "output" / "logs" / ".synth_done"

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def log(msg: str):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"  [{ts}] {msg}", flush=True)


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text('utf-8'))
    return {}


def save_progress(progress: dict):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=2), 'utf-8')


def get_domain_for_category(category: str) -> str:
    """从分类名推断知识域"""
    mapping = {
        '量化投资': 'finance_investment',
        '投资': 'finance_investment',
        '地缘经济': 'geoeconomics',
        '国际分析': 'intl_analysis',
        '中国政经': 'china_politics',
        '地缘政治': 'geopolitics',
        '短视频': 'short_video_directing',
    }
    for keyword, domain in mapping.items():
        if keyword in category:
            return domain
    return 'general'


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


# Pipeline 阶段标记（用于断点恢复）
STAGE_DOWNLOADING = 'downloading'
STAGE_TRANSCRIBED = 'transcribed'
STAGE_GENERATING = 'generating'
STAGE_GENERATED = 'generated'        # ← 当前使用中
STAGE_QUALITY_PASSED = 'quality_passed'
STAGE_SYNCED = 'synced'


# ============================================================
# 阶段 1: 补全已有转写
# ============================================================
def catch_up(progress: dict) -> tuple[int, int]:
    """为有转写但无笔记的文件生成笔记"""
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
    success = 0
    failed = 0
    for i, (stem, t_path) in enumerate(missing, 1):
        log(f"  [{i}/{len(missing)}] {stem}")
        cmd = [PYTHON, ENGINE, '--input', t_path]
        rc, out, err = run_cmd(cmd, timeout=600)
        if rc == 0:
            success += 1
            progress[f'catchup:{stem}'] = {'status': 'success', 'ts': datetime.now().isoformat()}
        else:
            failed += 1
            progress[f'catchup:{stem}'] = {'status': 'failed', 'error': err[-200:], 'ts': datetime.now().isoformat()}
        save_progress(progress)

    log(f"补全完成: {success} 成功, {failed} 失败")
    return success, failed


# ============================================================
# 阶段 2: 处理新视频
# ============================================================
MAX_AUTO_RETRIES = 2  # 网络/超时错误自动重试次数

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
    """逐个处理视频 URL，支持错误分类和自动重试"""
    todo = []
    for item in urls:
        key = item['url']
        if progress.get(key, {}).get('status') == 'success':
            continue
        todo.append(item)

    if max_count > 0:
        todo = todo[:max_count]

    if not todo:
        log("所有视频已处理完成")
        return 0, 0

    log(f"待处理: {len(todo)} 个视频")
    success = 0
    failed = 0
    since_sync = 0
    domain_new_notes = defaultdict(list)  # 域 → 新增笔记路径

    for i, item in enumerate(todo, 1):
        url = item['url']
        cat = item['category']
        ct = get_content_type(cat)

        log(f"[{i}/{len(todo)}] {url} ({cat or '未分类'})")

        # 带自动重试的处理
        final_result = None
        for attempt in range(1 + MAX_AUTO_RETRIES):
            start = time.time()
            cmd = [PYTHON, ENGINE, '--bilibili', url, '--content-type', ct]
            rc, out, err = run_cmd(cmd, timeout=2400)
            elapsed = time.time() - start

            if rc == 0:
                final_result = {
                    'status': 'success',
                    'stage': STAGE_GENERATED,
                    'category': cat,
                    'elapsed': round(elapsed, 1),
                    'ts': datetime.now().isoformat(),
                }
                break

            # 分类错误
            err_type = classify_error(err, out)

            # 不可重试的错误
            if err_type in ('deleted', 'too_short'):
                log(f"  ⏭️ 跳过 ({err_type})")
                final_result = {
                    'status': 'skipped',
                    'category': cat,
                    'reason': err_type,
                    'ts': datetime.now().isoformat(),
                }
                break

            # 可重试的错误
            if err_type in ('network', 'timeout') and attempt < MAX_AUTO_RETRIES:
                log(f"  ⚠️ {err_type}，自动重试 {attempt+1}/{MAX_AUTO_RETRIES}...")
                time.sleep(30)
                continue

            # 最终失败
            final_result = {
                'status': 'failed',
                'category': cat,
                'error_type': err_type,
                'error': err[-300:] if err else out[-300:],
                'elapsed': round(elapsed, 1),
                'ts': datetime.now().isoformat(),
            }

        # 记录结果
        progress[url] = final_result
        save_progress(progress)

        if final_result['status'] == 'success':
            success += 1
            since_sync += 1
            log(f"  ✅ 成功 ({final_result.get('elapsed', 0):.0f}秒)")
            # 记录新笔记，按域分组
            domain = get_domain_for_category(cat)
            if domain != 'general':
                domain_new_notes[domain].append(url)
                # 域内新笔记达到阈值，触发增量合成
                if len(domain_new_notes[domain]) >= BATCH_SIZE_FOR_SYNTH:
                    _incremental_synthesize(domain)
                    domain_new_notes[domain] = []
        elif final_result['status'] == 'skipped':
            pass  # 不计入 failed
        else:
            failed += 1
            log(f"  ❌ {final_result.get('error_type', 'unknown')}: {final_result.get('error', '')[:100]}")

        save_progress(progress)

        # 每 N 个成功后同步飞书（no_sync 时跳过）
        if not no_sync and since_sync >= BATCH_SIZE_FOR_SYNC:
            log(f"  📤 同步飞书（{since_sync} 个新笔记）...")
            _sync_feishu()
            since_sync = 0

    # 最后一批同步
    if since_sync > 0:
        log(f"  📤 同步飞书（最后 {since_sync} 个）...")
        _sync_feishu()

    return success, failed


# ============================================================
# 阶段 3: 飞书同步
# ============================================================
def _sync_feishu():
    cmd = ['py', '-3', SYNC_SCRIPT, '--new-only']
    rc, out, err = run_cmd(cmd, timeout=300)
    if rc == 0:
        log("  ✅ 飞书同步完成")
    else:
        log(f"  ⚠️ 飞书同步失败: {err[:100]}")


def _incremental_synthesize(domain: str):
    """对指定域执行增量合成（不重建，只更新）"""
    log(f"  🔬 域 '{domain}' 新笔记达阈值，触发增量合成...")
    cmd = [PYTHON, ENGINE, '--mode', 'synthesis-2stage', '--domain', domain, '--batch']
    rc, out, err = run_cmd(cmd, timeout=600)
    if rc == 0:
        log(f"  ✅ {domain} 增量合成完成")
    else:
        log(f"  ⚠️ {domain} 合成失败: {err[:80] or out[-80:]}")


def health_check() -> bool:
    """批量前健康检查：验证各组件可用"""
    log("🏥 健康检查...")
    checks = []

    # 1. Python 环境
    rc, out, err = run_cmd([PYTHON, '--version'], timeout=10)
    checks.append(('Python 环境', rc == 0))

    # 2. 引擎脚本
    checks.append(('引擎脚本', ENGINE.exists()))

    # 3. 配置文件
    config_path = PROJECT_ROOT / 'config' / 'llm_engine_config.yaml'
    checks.append(('配置文件', config_path.exists()))

    # 4. LLM 代理可达
    try:
        import requests
        resp = requests.get('http://127.0.0.1:15721', timeout=5)
        checks.append(('LLM 代理', True))
    except Exception:
        checks.append(('LLM 代理', False))

    # 5. 飞书同步脚本
    checks.append(('飞书同步', Path(SYNC_SCRIPT).exists()))

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
        domain = _detect_domain_from_name(stem)
        domain_notes[domain].append(str(note_path))

    synth_count = 0
    done_domains = set()
    if SYNTH_DONE_FLAG.exists():
        done_domains = set(SYNTH_DONE_FLAG.read_text('utf-8').strip().split('\n'))

    for domain, notes in domain_notes.items():
        if len(notes) < BATCH_SIZE_FOR_SYNTH:
            log(f"  域 '{domain}': {len(notes)} 篇（不足 {BATCH_SIZE_FOR_SYNTH} 篇，跳过合成）")
            continue
        if domain in done_domains:
            log(f"  域 '{domain}': 已合成过，跳过")
            continue
        if domain == 'general':
            log(f"  域 'general': 兜底域，跳过合成")
            continue

        log(f"🔬 域 '{domain}' 有 {len(notes)} 篇笔记，独立触发跨集合成...")
        # 指定 domain 参数，确保只合成该域的笔记，不跨域整合
        cmd = [PYTHON, ENGINE, '--mode', 'synthesis-2stage', '--domain', domain]
        rc, out, err = run_cmd(cmd, timeout=600)
        if rc == 0:
            log(f"  ✅ {domain} 合成完成")
            synth_count += 1
            done_domains.add(domain)
            SYNTH_DONE_FLAG.parent.mkdir(parents=True, exist_ok=True)
            SYNTH_DONE_FLAG.write_text('\n'.join(done_domains), 'utf-8')
        else:
            log(f"  ⚠️ {domain} 合成失败: {err[:100] or out[-100:]}")

    return synth_count


def _detect_domain_from_name(name: str) -> str:
    keywords = {
        'finance_investment': ['投资', '量化', '基金', '策略', '因子', '概率', '胜率', '超额', '收益', '回撤'],
        'geoeconomics': ['地缘', '制裁', '贸易战', '关税', '能源', '冲突', '战争', '脱钩'],
        'intl_analysis': ['国际', '美国', '欧洲', '全球化', '格局', '霸权', '外交', '中东'],
        'china_politics': ['中国', '房产', '内需', '消费', '改革', '政策', '央行', 'GDP', '通胀', '利率'],
        'geopolitics': ['中美', '博弈', '翟东升', '货币', '美元', '美债', '正在发生'],
        'short_video_directing': ['导演', '短视频', '拍摄', '剪辑', '第', '集'],
    }
    for domain, kws in keywords.items():
        if any(kw in name for kw in kws):
            return domain
    return 'general'


# ============================================================
# 主流程
# ============================================================
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
