# -*- coding: utf-8 -*-
"""
NoteForge 批量处理模块
提取自 llm_note_engine.py 的批量生成与汇总打印逻辑
"""

import os
import time
from pathlib import Path
from typing import List, Optional, Callable

from noteforge.models import GenerationResult


class BatchProcessor:
    """批量笔记生成处理器"""

    def __init__(self, path_config, logger, token_manager=None):
        """
        Args:
            path_config: PathConfig 共享路径配置（持有引用，路径变更自动同步）
            logger: 日志记录器
            token_manager: TokenManager 实例（用于汇总成本统计）
        """
        self._path_config = path_config
        self.logger = logger
        self._token_manager = token_manager

    # 兼容属性（委托到 _path_config）
    @property
    def _notes_dir(self):
        return self._path_config.notes_dir

    @property
    def _transcripts_dir(self):
        return self._path_config.transcripts_dir

    def generate_batch(
        self,
        transcript_paths: Optional[List[str]] = None,
        generate_note_fn: Optional[Callable] = None,
        skip_existing: bool = True,
        provider_override: Optional[str] = None,
        force: bool = False,
        mode: str = 'notes',
        with_context: bool = False,
        context_limit: int = 3,
    ) -> List[GenerationResult]:
        """
        批量生成笔记

        Args:
            transcript_paths: 转写文件路径列表（默认处理所有）
            generate_note_fn: 单篇生成回调，签名 (tpath, output_path, ...) -> GenerationResult
            skip_existing: 是否跳过已有笔记
            provider_override: 覆盖提供商
            force: 是否覆盖已有笔记
            with_context: 是否注入上下文笔记
            context_limit: 上下文笔记数量上限

        Returns:
            结果列表
        """
        if transcript_paths is None:
            transcript_paths = sorted(
                str(p) for p in self._transcripts_dir.glob('*.txt')
            )

        if not transcript_paths:
            self.logger.warning("未找到转写文件")
            return []

        self.logger.info(f"批量生成: {len(transcript_paths)} 个文件")
        results: List[GenerationResult] = []

        for i, tpath in enumerate(transcript_paths, 1):
            stem = Path(tpath).stem
            output_path = str(self._notes_dir / f"{stem}.md")

            if skip_existing and os.path.exists(output_path) and not force:
                self.logger.info(f"[{i}/{len(transcript_paths)}] 跳过已有: {stem}")
                results.append(GenerationResult(
                    transcript_path=tpath,
                    note_path=output_path,
                    error="已存在（跳过）"
                ))
                continue

            self.logger.info(f"[{i}/{len(transcript_paths)}] 处理: {stem}")
            result = generate_note_fn(
                tpath, output_path=output_path,
                provider_override=provider_override, force=force,
                mode=mode, with_context=with_context,
                context_limit=context_limit
            )
            results.append(result)

            # 批量处理间的短暂间隔，避免 API 限流
            if i < len(transcript_paths):
                time.sleep(2)

        # 打印汇总报告
        self.print_batch_summary(results)
        return results

    def print_batch_summary(self, results: List[GenerationResult]):
        """打印批量处理汇总"""
        passed = [r for r in results if r.overall_passed]
        failed = [r for r in results if not r.overall_passed and not r.error]
        errors = [r for r in results if r.error]
        skipped = [r for r in results if r.error and "已存在" in r.error]

        print("\n" + "=" * 60)
        print("  📊 批量生成汇总")
        print("=" * 60)
        print(f"  ✅ 质量通过: {len(passed)}")
        print(f"  ⚠️  质量未达标: {len(failed)}")
        print(f"  ⏭️  跳过: {len(skipped)}")
        print(f"  ❌ 错误: {len(errors) - len(skipped)}")

        if passed:
            avg_score = sum(r.total_score for r in passed) / len(passed)
            print(f"\n  📈 通过平均分: {avg_score:.0%}")

        total_time = sum(r.duration_seconds for r in results)
        print(f"  ⏱️  总耗时: {total_time:.0f}秒 ({total_time / 60:.1f}分钟)")

        # Token 使用量汇总
        total_input = sum(r.token_usage.get('input_tokens', 0) for r in results)
        total_output = sum(r.token_usage.get('output_tokens', 0) for r in results)
        total_calls = sum(r.token_usage.get('calls', 0) for r in results)
        if total_input > 0 or total_output > 0:
            print(f"\n  🔢 Token 消耗:")
            print(f"     Input:  {total_input:>10,}")
            print(f"     Output: {total_output:>10,}")
            print(f"     LLM 调用: {total_calls} 次")

        # TokenManager 成本统计
        if self._token_manager is not None:
            tm_summary = self._token_manager.get_summary()
            if tm_summary.get('total_cost', 0) > 0:
                print(f"\n  💰 成本统计:")
                print(f"     总成本: ${tm_summary['total_cost']:.4f}")
                if tm_summary.get('total_cached', 0) > 0:
                    print(f"     缓存命中: {tm_summary['total_cached']:,} tokens")
                self._token_manager.print_summary()

        if failed:
            print(f"\n  ⚠️  未达标详情:")
            for r in failed:
                stem = Path(r.transcript_path).stem
                print(f"     {stem}: {r.total_score:.0%}")

        if errors and len(errors) > len(skipped):
            print(f"\n  ❌ 错误详情:")
            for r in errors:
                if r.error != "已存在（跳过）":
                    stem = Path(r.transcript_path).stem
                    print(f"     {stem}: {r.error}")

        print("=" * 60)
