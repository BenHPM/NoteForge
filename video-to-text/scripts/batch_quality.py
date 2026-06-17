"""
batch_quality.py — 批量质量评分脚本
对 output/notes/ 下所有笔记运行 quality_gate 评分，生成 quality_reports

用法:
    python batch_quality.py               # 评分所有笔记
    python batch_quality.py --skip-existing  # 跳过已有报告的笔记
    python batch_quality.py --dry-run        # 只显示映射，不实际评分
"""

import os
import sys
import json
import logging
import argparse
import re
from pathlib import Path
from datetime import datetime

# 修复 Windows 控制台编码
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 添加 scripts 目录到 path
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from quality_gate import QualityGate


def load_video_mapping(base_dir: Path) -> list[dict]:
    """加载 video-mapping.json"""
    config_path = base_dir / "config" / "video-mapping.json"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def normalize(s: str) -> str:
    """标准化字符串用于模糊匹配"""
    return re.sub(r'[：:、，,。.！!？?\-\s_（）()\[\]]', '', s)


def find_transcript_for_note(
    note_path: Path,
    transcripts_dir: Path,
    video_mapping: list[dict],
) -> Path | None:
    """为笔记文件找到对应的转写文件"""
    stem = note_path.stem

    # 1. 直接匹配
    direct = transcripts_dir / f"{stem}.txt"
    if direct.exists():
        return direct

    # 2. 通过 video-mapping.json 匹配
    stem_norm = normalize(stem)
    for item in video_mapping:
        title = item.get('title', '')
        title_norm = normalize(title)
        if title_norm and (title_norm == stem_norm or title_norm in stem_norm or stem_norm in title_norm):
            ep_id = item.get('id', '')
            if ep_id:
                t_path = transcripts_dir / f"{ep_id}.txt"
                if t_path.exists():
                    return t_path

    # 3. 提取集数编号匹配 epXX
    ep_match = re.search(r'第(\d+)集', stem)
    if ep_match:
        ep_num = int(ep_match.group(1))
        # 检查是否有实操/理论后缀
        is_theory = '理论' in stem
        is_practice = '实操' in stem
        is_extra = '花絮' in stem

        # 根据 video-mapping 找对应 epXX
        for item in video_mapping:
            item_title = item.get('title', '')
            item_ep_match = re.search(r'第(\d+)集', item_title)
            if item_ep_match and int(item_ep_match.group(1)) == ep_num:
                # 检查后缀匹配
                item_has_theory = '理论' in item_title
                item_has_practice = '实操' in item_title
                item_has_extra = '花絮' in item_title

                if is_theory == item_has_theory and is_practice == item_has_practice and is_extra == item_has_extra:
                    ep_id = item.get('id', '')
                    if ep_id:
                        t_path = transcripts_dir / f"{ep_id}.txt"
                        if t_path.exists():
                            return t_path

    # 4. 模糊匹配
    for t_file in sorted(transcripts_dir.glob('*.txt')):
        t_stem = t_file.stem
        if t_stem in stem or stem in t_stem:
            return t_file

    return None


def main():
    parser = argparse.ArgumentParser(description='NoteForge 批量质量评分')
    parser.add_argument('--skip-existing', action='store_true', help='跳过已有质量报告的笔记')
    parser.add_argument('--dry-run', action='store_true', help='只显示映射，不实际评分')
    parser.add_argument('--notes-dir', type=str, default=None, help='笔记目录（默认 output/notes）')
    parser.add_argument('--transcripts-dir', type=str, default=None, help='转写目录（默认 output/transcripts）')
    args = parser.parse_args()

    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
    )
    logger = logging.getLogger('batch_quality')

    # 路径
    base_dir = BASE_DIR
    notes_dir = Path(args.notes_dir) if args.notes_dir else base_dir / "output" / "notes"
    transcripts_dir = Path(args.transcripts_dir) if args.transcripts_dir else base_dir / "output" / "transcripts"
    reports_dir = base_dir / "output" / "quality_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    if not notes_dir.exists():
        logger.error(f"笔记目录不存在: {notes_dir}")
        sys.exit(1)

    # 加载映射
    video_mapping = load_video_mapping(base_dir)
    logger.info(f"加载 video-mapping: {len(video_mapping)} 条")

    # 初始化质量门禁
    gate = QualityGate()

    # 扫描笔记
    note_files = sorted(notes_dir.glob('*.md'))
    # 跳过知识体系等合成产物
    note_files = [f for f in note_files if not f.stem.startswith(('knowledge_synthesis', 'mental_models', 'action_playbook'))]

    logger.info(f"找到 {len(note_files)} 个笔记文件")

    # 处理每个笔记
    results = []
    skipped = 0
    errors = 0

    for note_path in note_files:
        stem = note_path.stem
        report_path = reports_dir / f"{stem}_quality.json"

        # 跳过已有报告
        if args.skip_existing and report_path.exists():
            skipped += 1
            continue

        # 找对应转写文件
        transcript_path = find_transcript_for_note(note_path, transcripts_dir, video_mapping)

        if args.dry_run:
            status = "✓ 有转写" if transcript_path else "✗ 无转写"
            report_status = "已有报告" if report_path.exists() else "待评分"
            print(f"  {stem}: {status}, {report_status}")
            if transcript_path:
                print(f"    → {transcript_path.name}")
            continue

        if not transcript_path:
            logger.warning(f"跳过 {stem}: 未找到对应转写文件")
            errors += 1
            continue

        # 运行质量评分
        try:
            logger.info(f"评分: {stem}")
            report = gate.evaluate(str(note_path), str(transcript_path))

            # 保存报告
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)

            passed = "PASS" if report.overall_passed else "FAIL"
            logger.info(f"  → 总分: {report.total_score:.2f} [{passed}]")

            results.append({
                'note': stem,
                'score': report.total_score,
                'passed': report.overall_passed,
                'report': str(report_path),
            })

        except Exception as e:
            logger.error(f"评分失败 {stem}: {e}")
            errors += 1

    # 汇总
    if args.dry_run:
        print(f"\n[Dry-run] 共 {len(note_files)} 个笔记，{skipped} 个已有报告")
        return

    print("\n" + "=" * 60)
    print("批量质量评分汇总")
    print("=" * 60)
    print(f"总计: {len(note_files)} 个笔记")
    print(f"已评分: {len(results)}")
    print(f"跳过: {skipped}")
    print(f"失败: {errors}")

    if results:
        passed = [r for r in results if r['passed']]
        failed = [r for r in results if not r['passed']]
        print(f"通过: {len(passed)}")
        print(f"未通过: {len(failed)}")

        if failed:
            print("\n未通过的笔记:")
            for r in failed:
                print(f"  - {r['note']}: {r['score']:.2f}")

        avg_score = sum(r['score'] for r in results) / len(results)
        print(f"\n平均分: {avg_score:.2f}")

    print(f"\n报告已保存到: {reports_dir}")


if __name__ == '__main__':
    main()
