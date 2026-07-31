# -*- coding: utf-8 -*-
"""知识域 CLI 命令：检测、列表、增量更新"""
import json
import os
import sys
from pathlib import Path

from noteforge.config import NoteForgeConfig
from noteforge.core.domain_classifier import DomainClassifier
from noteforge.infra.file_io import read_file

# 项目根目录: domain.py -> commands -> cli -> noteforge -> video-to-text/
_BASE_DIR = Path(__file__).parent.parent.parent.parent
_CONFIG_PATH = str(_BASE_DIR / "config" / "llm_engine_config.yaml")


def _load_domains():
    """加载知识域配置列表"""
    config_mgr = NoteForgeConfig(config_path=_CONFIG_PATH, base_dir=_BASE_DIR)
    return config_mgr.raw.get('knowledge_domains', []), config_mgr


def run_detect_domain(args):
    """检测文件所属知识域（不需要引擎，直接用 DomainClassifier）"""
    filepath = args.detect_domain
    if not os.path.exists(filepath):
        print(f"[ERROR] 文件不存在: {filepath}")
        return 1

    # 加载配置
    domains, config_mgr = _load_domains()

    classifier = DomainClassifier(domains=domains, path_config=config_mgr.path_config)
    domain_id = classifier.detect_domain(filepath)
    domain_config = classifier.get_domain_config(domain_id)

    # 计算匹配关键词（用于展示）
    stem = os.path.basename(filepath)
    stem_lower = os.path.splitext(stem)[0].lower()
    try:
        content_lower = read_file(filepath)[:5000].lower()
    except Exception:
        content_lower = ""

    matched_keywords = []
    if domain_id != 'general':
        keywords = domain_config.get('match_keywords', [])
        matched_keywords = [kw for kw in keywords
                           if kw.lower() in stem_lower or kw.lower() in content_lower]

    # 计算置信度（简化：基于匹配关键词占比）
    total_kw = len(domain_config.get('match_keywords', []))
    if total_kw > 0 and matched_keywords:
        confidence = len(matched_keywords) / total_kw
    elif domain_id == 'general':
        confidence = 0.0
    else:
        confidence = 0.0

    domain_name = domain_config.get('name', domain_config.get('output_name', domain_id))

    print(f"文件: {filepath}")
    print(f"知识域 ID: {domain_id}")
    print(f"知识域名称: {domain_name}")
    print(f"匹配关键词: {', '.join(matched_keywords) if matched_keywords else '(无直接匹配，使用兜底)'}")
    print(f"置信度: {confidence:.1%}")
    return 0


def run_domain_list(args):
    """列出所有已配置的知识域（不需要引擎，直接读配置）"""
    domains, _ = _load_domains()

    if not domains:
        print("[INFO] 未配置知识域")
        return 0

    fmt = getattr(args, 'format', 'table')

    if fmt == 'json':
        print(json.dumps(domains, ensure_ascii=False, indent=2))
        return 0

    # 表格输出
    # 计算列宽
    id_w = max(len(d.get('id', '')) for d in domains)
    id_w = max(id_w, len('domain_id'))
    kw_w = 40  # 关键词列截断
    ex_w = 30  # 排除词列截断
    mf_w = 30  # 匹配文件列截断

    header = f"{'domain_id':<{id_w}}  {'keywords':<{kw_w}}  {'exclude':<{ex_w}}  {'match_files':<{mf_w}}"
    print(header)
    print('-' * len(header))

    for d in domains:
        did = d.get('id', '')
        keywords = ', '.join(d.get('match_keywords', []))
        if len(keywords) > kw_w:
            keywords = keywords[:kw_w - 3] + '...'
        excludes = ', '.join(d.get('exclude_keywords', []))
        if len(excludes) > ex_w:
            excludes = excludes[:ex_w - 3] + '...'
        match_files = ', '.join(d.get('match_files', []))
        if len(match_files) > mf_w:
            match_files = match_files[:mf_w - 3] + '...'
        print(f"{did:<{id_w}}  {keywords:<{kw_w}}  {excludes:<{ex_w}}  {match_files:<{mf_w}}")

    return 0


def run_incremental_update(engine, args):
    """按知识域运行增量合成更新"""
    domain_id = getattr(args, 'domain', None)
    if not domain_id:
        print("[ERROR] 增量更新需要指定知识域 ID (--domain)")
        return 1

    # 验证域存在
    domain_config = engine.get_domain_config(domain_id)
    if domain_config.get('id') == 'general' and domain_id != 'general':
        print(f"[ERROR] 未知知识域: {domain_id}")
        print("[INFO] 使用 --domain-list 查看所有可用知识域")
        return 1

    # 获取该域的所有笔记
    notes_by_domain = engine.get_notes_by_domain()
    domain_notes = notes_by_domain.get(domain_id, [])

    if not domain_notes:
        print(f"[INFO] 知识域 '{domain_id}' 没有笔记，无需增量更新")
        return 0

    # 找到最新笔记作为增量输入
    latest_note = max(domain_notes, key=lambda p: os.path.getmtime(p)
                      if os.path.exists(p) else 0)

    print(f"[INFO] 知识域: {domain_id}")
    print(f"[INFO] 域内笔记数: {len(domain_notes)}")
    print(f"[INFO] 增量输入: {os.path.basename(latest_note)}")

    result = engine.update_synthesis_incremental(
        new_note_path=latest_note,
        provider_override=getattr(args, 'provider', None),
    )

    if result:
        print(f"\n[OK] 增量更新完成: {result}")
        engine.token_manager.print_summary()
        return 0
    else:
        print("\n[ERROR] 增量更新失败")
        return 1
