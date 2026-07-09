# -*- coding: utf-8 -*-
"""共享辅助函数"""
import os

from noteforge.sources.downloader import MediaDownloader


def _show_cached_quality(engine, note_path: str):
    """缓存跳过时自动运行质量检查并显示摘要"""
    if not note_path or not os.path.exists(note_path):
        return
    print(f"  [INFO] 笔记已存在: {os.path.basename(note_path)}")
    try:
        report = engine.check_only(note_path)
        if report:
            score = report.get('total_score', 0)
            passed = report.get('overall_passed', False)
            print(f"  [质量] 总分: {score:.0%} | {'✅ 通过' if passed else '❌ 未通过'}")
    except Exception:
        pass  # 质量检查失败不影响主流程
