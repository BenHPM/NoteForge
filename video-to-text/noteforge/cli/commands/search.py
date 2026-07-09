# -*- coding: utf-8 -*-
"""搜索与列表命令"""


def run_search(engine, args):
    """笔记搜索"""
    from noteforge.intelligence.knowledge_index import KnowledgeIndex
    idx = KnowledgeIndex(str(engine.notes_dir))
    results = idx.search(args.search, tags=args.tags)
    if not results:
        print(f"\n未找到匹配 '{args.search}' 的笔记")
    else:
        print(f"\n搜索 '{args.search}' 找到 {len(results)} 条结果:\n")
        for i, r in enumerate(results, 1):
            print(f"  {i}. [{r.date}] {r.title}")
            print(f"     相关度: {r.relevance:.0%} | 标签: {', '.join(r.tags[:5])}")
            print(f"     {r.snippet[:120]}")
            print()
    return 0


def run_list_notes(engine, args):
    """笔记库概览"""
    from noteforge.intelligence.knowledge_index import KnowledgeIndex
    idx = KnowledgeIndex(str(engine.notes_dir))
    notes = idx.list_notes()
    tags = idx.get_all_tags()
    if not notes:
        print("\n笔记库为空")
    else:
        print(f"\n{'='*60}")
        print(f"  笔记库概览 ({len(notes)} 篇)")
        print(f"{'='*60}\n")
        for n in notes:
            print(f"  [{n.date}] {n.title}")
            print(f"     {n.char_count} 字 | 框架: {len(n.key_frameworks)} | 行动项: {len(n.action_items)}")
            if n.tags:
                print(f"     标签: {', '.join(n.tags[:5])}")
        if tags:
            print(f"\n  --- 热门标签 ---")
            tag_str = ' | '.join(f"{t}({c})" for t, c in list(tags.items())[:15])
            print(f"  {tag_str}")
        print(f"\n{'='*60}")
    return 0
