# -*- coding: utf-8 -*-
"""
Bilibili 批量处理脚本
支持断点续传、失败重试、进度追踪

用法:
  python batch_bilibili.py urls.txt              # 处理列表
  python batch_bilibili.py urls.txt --resume     # 断点续传
  python batch_bilibili.py urls.txt --dry-run    # 预览
  python batch_bilibili.py urls.txt --force      # 强制重新处理

urls.txt 格式（每行一个 URL，# 开头为注释）:
  # 地缘经济
  https://www.bilibili.com/video/BV1xxx/
  https://www.bilibili.com/video/BV2xxx/
  # 量化投资
  https://www.bilibili.com/video/BV3xxx/
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime

# 项目路径
PROJECT_ROOT = Path(__file__).resolve().parent
ENGINE_SCRIPT = PROJECT_ROOT / "scripts" / "llm_note_engine.py"
PYTHON_EXE = PROJECT_ROOT / "envs" / "paraformer" / "python.exe"
PROGRESS_FILE = PROJECT_ROOT / "output" / "logs" / "batch_progress.json"

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def load_urls(filepath: str) -> list[dict]:
    """加载 URL 列表，支持 # 注释和分类标注"""
    urls = []
    current_category = ""
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith('#'):
                current_category = line.lstrip('# ').strip()
                continue
            if 'bilibili.com/video/' in line or line.startswith('BV'):
                urls.append({
                    'url': line.split('?')[0],  # 去掉查询参数
                    'category': current_category,
                })
    return urls


def load_progress() -> dict:
    """加载进度"""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_progress(progress: dict):
    """保存进度"""
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def process_one(url: str, category: str, force: bool = False, dry_run: bool = False) -> dict:
    """处理单个视频，返回结果"""
    if dry_run:
        return {'status': 'dry-run', 'url': url}

    cmd = [
        str(PYTHON_EXE), str(ENGINE_SCRIPT),
        '--bilibili', url,
    ]
    if force:
        cmd.append('--force')

    # 根据分类推断 content_type
    if any(k in category for k in ['投资', '量化', '基金', '策略']):
        cmd.extend(['--content-type', 'interview'])
    elif any(k in category for k in ['地缘', '国际', '分析', '经济']):
        cmd.extend(['--content-type', 'lecture'])

    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=1800,  # 30 分钟超时
        )
        elapsed = time.time() - start

        if result.returncode == 0:
            return {
                'status': 'success',
                'elapsed': round(elapsed, 1),
                'output': result.stdout[-500:] if result.stdout else '',
            }
        else:
            return {
                'status': 'failed',
                'elapsed': round(elapsed, 1),
                'error': result.stderr[-500:] if result.stderr else '',
                'output': result.stdout[-300:] if result.stdout else '',
            }
    except subprocess.TimeoutExpired:
        return {
            'status': 'timeout',
            'elapsed': 1800,
        }
    except Exception as e:
        return {
            'status': 'error',
            'error': str(e),
        }


def main():
    parser = argparse.ArgumentParser(description='Bilibili 批量处理')
    parser.add_argument('urls_file', help='URL 列表文件')
    parser.add_argument('--resume', action='store_true', help='断点续传（跳过已完成）')
    parser.add_argument('--force', action='store_true', help='强制重新处理')
    parser.add_argument('--dry-run', action='store_true', help='预览模式')
    parser.add_argument('--max', type=int, default=0, help='最多处理 N 个（0=不限）')
    args = parser.parse_args()

    # 加载 URL
    urls = load_urls(args.urls_file)
    if not urls:
        print("[ERROR] 未找到有效 URL")
        sys.exit(1)

    # 加载进度
    progress = load_progress() if args.resume else {}

    # 统计
    total = len(urls)
    done = sum(1 for u in urls if progress.get(u['url'], {}).get('status') == 'success')
    todo = [u for u in urls if progress.get(u['url'], {}).get('status') != 'success']

    if args.max > 0:
        todo = todo[:args.max]

    print("=" * 60)
    print(f"  Bilibili 批量处理")
    print(f"  文件: {args.urls_file}")
    print(f"  总计: {total} | 已完成: {done} | 待处理: {len(todo)}")
    print(f"  模式: {'DRY-RUN' if args.dry_run else '正式'}")
    print("=" * 60)

    if not todo:
        print("\n所有视频已处理完成！")
        return

    # 按分类统计
    categories = {}
    for u in todo:
        cat = u['category'] or '未分类'
        categories[cat] = categories.get(cat, 0) + 1
    print(f"\n待处理分类:")
    for cat, count in categories.items():
        print(f"  - {cat}: {count} 个")

    # 处理
    success = 0
    failed = 0
    for i, item in enumerate(todo, 1):
        url = item['url']
        cat = item['category']
        print(f"\n{'='*60}")
        print(f"  [{i}/{len(todo)}] {url}")
        print(f"  分类: {cat or '未分类'}")
        print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")

        result = process_one(url, cat, force=args.force, dry_run=args.dry_run)

        # 记录进度
        progress[url] = {
            **result,
            'category': cat,
            'timestamp': datetime.now().isoformat(),
        }
        save_progress(progress)

        if result['status'] == 'success':
            success += 1
            print(f"  ✅ 成功 ({result.get('elapsed', 0):.0f}秒)")
        elif result['status'] == 'dry-run':
            print(f"  [DRY-RUN] 跳过")
        else:
            failed += 1
            print(f"  ❌ {result['status']}: {result.get('error', '')[:200]}")

        # 进度汇总
        print(f"\n  进度: {done + success}/{total} 成功, {failed} 失败")

    # 最终汇总
    print(f"\n{'='*60}")
    print(f"  批量处理完成！")
    print(f"  成功: {success} | 失败: {failed} | 总计: {done + success}/{total}")
    print(f"  进度文件: {PROGRESS_FILE}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
