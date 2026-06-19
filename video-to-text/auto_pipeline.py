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
import glob
from pathlib import Path
from datetime import datetime
from collections import defaultdict

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


def load_urls(filepath: str) -> list[dict]:
    urls = []
    current_category = ""
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith('#'):
                content = line.lstrip('# ').strip()
                if '|' not in content and len(content) < 30:
                    current_category = content
                continue
            if 'bilibili.com/video/' in line or line.startswith('BV'):
                urls.append({
                    'url': line.split('?')[0],
                    'category': current_category,
                })
    return urls


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


def run_cmd(cmd: list, timeout: int = 1800) -> tuple[int, str, str]:
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
def process_videos(urls: list[dict], progress: dict, max_count: int = 0) -> tuple[int, int]:
    """逐个处理视频 URL"""
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

    for i, item in enumerate(todo, 1):
        url = item['url']
        cat = item['category']
        ct = get_content_type(cat)

        log(f"[{i}/{len(todo)}] {url} ({cat or '未分类'})")
        start = time.time()

        cmd = [PYTHON, ENGINE, '--bilibili', url, '--content-type', ct]
        rc, out, err = run_cmd(cmd, timeout=1800)
        elapsed = time.time() - start

        if rc == 0:
            success += 1
            since_sync += 1
            progress[url] = {
                'status': 'success',
                'category': cat,
                'elapsed': round(elapsed, 1),
                'ts': datetime.now().isoformat(),
            }
            log(f"  ✅ 成功 ({elapsed:.0f}秒)")
        else:
            failed += 1
            progress[url] = {
                'status': 'failed',
                'category': cat,
                'error': err[-300:] if err else out[-300:],
                'ts': datetime.now().isoformat(),
            }
            log(f"  ❌ 失败: {(err or out)[:150]}")

        save_progress(progress)

        # 每 N 个成功后同步飞书
        if since_sync >= BATCH_SIZE_FOR_SYNC:
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


# ============================================================
# 阶段 4: 跨集合成
# ============================================================
def auto_synthesize(progress: dict) -> int:
    """按域统计笔记数量，达到阈值的自动触发跨集合成"""
    # 统计每个域的笔记数
    domain_notes = defaultdict(list)
    for note_path in NOTES_DIR.glob('*.md'):
        stem = note_path.stem
        if any(k in stem for k in ['知识体系', '_v2', '_v3', '_v4', '_incremental', 'extractions']):
            continue
        # 从文件名推断域
        domain = _detect_domain_from_name(stem)
        domain_notes[domain].append(str(note_path))

    synth_count = 0
    done_domains = set()
    if SYNTH_DONE_FLAG.exists():
        done_domains = set(SYNTH_DONE_FLAG.read_text('utf-8').strip().split('\n'))

    for domain, notes in domain_notes.items():
        if len(notes) < BATCH_SIZE_FOR_SYNTH:
            continue
        if domain in done_domains:
            continue
        if domain == 'general':
            continue  # 不自动合成兜底域

        log(f"🔬 域 '{domain}' 有 {len(notes)} 篇笔记，触发跨集合成...")
        cmd = [PYTHON, ENGINE, '--mode', 'synthesis-2stage']
        rc, out, err = run_cmd(cmd, timeout=600)
        if rc == 0:
            log(f"  ✅ {domain} 合成完成")
            synth_count += 1
            done_domains.add(domain)
            SYNTH_DONE_FLAG.parent.mkdir(parents=True, exist_ok=True)
            SYNTH_DONE_FLAG.write_text('\n'.join(done_domains), 'utf-8')
        else:
            log(f"  ⚠️ {domain} 合成失败: {err[:100]}")

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
    parser.add_argument('--max', type=int, default=0, help='最多处理 N 个视频')
    args = parser.parse_args()

    progress = load_progress() if args.resume else {}
    start_time = time.time()

    print("=" * 60)
    print("  NoteForge 自主执行流水线")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模式: {'续传' if args.resume else '首次'}")
    print("=" * 60)

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
                s, f = process_videos(urls, progress, args.max)
                log(f"  结果: {s} 成功, {f} 失败")

    # 阶段 3: 跨集合成
    log("📋 阶段 3: 检查跨集合成...")
    n = auto_synthesize(progress)
    if n > 0:
        log(f"  完成 {n} 个域的合成")

    # 最终飞书同步
    log("📋 最终飞书同步...")
    _sync_feishu()

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
