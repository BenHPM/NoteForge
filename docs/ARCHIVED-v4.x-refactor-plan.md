# NoteForge 架构重构实施计划 v2.0

> **状态**: ✅ 全部完成（阶段 1 + 查漏补缺 7 步，版本 v5.0.0）
> **日期**: 2026-07-08
> **原则**: 先建骨架（包结构+基础设施统一），再抽血（PipelineContext 解耦 CLI），最后动刀（engine 瘦身 + gate 拆分 + 测试补充）

---

## 当前状态快照

| 维度 | 数值 |
|------|------|
| scripts/*.py 总行数 | ~7,900 |
| 最大文件 | quality_gate.py (1544 行) |
| 第二大文件 | llm_note_engine.py (812 行) |
| sys.path.insert | 5 处（scripts/）+ 4 处（tests/） |
| 重复 _read_file | 6 处（engine/synthesis/domain_classifier/external_sync/audio_handler/quality_gate） |
| 重复 _write_file | 3 处（engine/synthesis/file_utils） |
| 死代码 | topic_classifier.py (404 行，零引用) |
| file_utils.py | 已存在但零被引用 |
| CLI 侵入 engine 内部 | 6 处 engine._xxx 赋值 |

---

## 目标包结构

```
video-to-text/
  noteforge/                          ← 新建 package
    __init__.py                       ← 版本 + 顶层 re-export
    models.py                         ← GenerationResult, TokenUsage（叶子节点）
    context.py                        ← PipelineContext
    config.py                         ← 配置加载 + 路径管理

    infra/                            ← 基础设施（稳定底层）
      __init__.py
      file_io.py                      ← 统一 read_file / write_file
      logging_setup.py                ← 从 logging_config.py 迁入
      colors.py                       ← 从 ansi_colors.py 迁入
      env.py                          ← 从 env_check.py 迁入

    core/                             ← 领域核心（精简）
      __init__.py
      llm_providers.py
      prompt_builder.py
      note_formatter.py
      token_manager.py
      transcript_preprocessor.py
      domain_classifier.py

    sources/                          ← 数据源（变化最快）
      __init__.py
      base.py                         ← Source ABC + FetchResult + SourceRegistry
      youtube.py
      bilibili.py
      podcast.py
      local.py                        ← 本地音频/视频
      downloader.py                   ← MediaDownloader（从 cli.py 提取）

    quality/                          ← 质量系统
      __init__.py
      gate.py                         ← R0-R12 评分（暂整体迁入）
      heuristics.py                   ← 启发式指标（从 gate.py 提取 ~200 行）
      manager.py
      batch.py                        ← 从 batch_quality.py 迁入

    intelligence/                     ← LLM + 合成
      __init__.py
      synthesis.py
      knowledge_index.py

    integration/                      ← 外部集成
      __init__.py
      feishu.py                       ← feishu_client.py
      sync.py                         ← external_sync.py

    engine/                           ← 编排层（逐步瘦身）
      __init__.py
      note_engine.py                  ← 当前 llm_note_engine.py
      pipeline.py                     ← Pipeline 编排器（新增）
      stages/                         ← Pipeline 阶段（逐步添加）
        __init__.py
        generate.py                   ← 质量反馈循环 + 分块生成

    batch/                            ← 批量处理
      __init__.py
      processor.py
      auto_pipeline.py
      bilibili.py

    cli/                              ← CLI 入口
      __init__.py
      main.py                         ← 用 PipelineContext 替代 engine._xxx

  scripts/                            ← 兼容 shim（过渡期）
    _compat.py                        ← 旧导入路径 re-export
    ... (旧文件保留，import 委托到 noteforge/)
```

## 依赖方向

```
cli/  →  engine/  →  intelligence/  →  core/  →  infra/  →  models
              ↓            ↓
          quality/     sources/
              ↓            ↓
           infra/       core/

强制规则：
- models 是叶子，零业务依赖
- infra 只依赖 models
- core 只依赖 infra + models
- sources/quality/intelligence 依赖 core + 更底层
- engine 依赖 quality/intelligence/sources/core
- cli 只依赖 engine（通过 PipelineContext 传参）
- 禁止反向依赖
```

---

## Commit 1：包骨架 + 基础设施统一（低风险，~1h）

### 目标
- 创建 noteforge/ 包骨架
- 统一文件 IO 到 infra/file_io.py
- 迁入其他基础设施模块
- 删除死代码 topic_classifier.py

### 步骤
- [x] 1.1 创建 noteforge/ 目录 + 所有子包 __init__.py
- [x] 1.2 创建 noteforge/models.py（从 scripts/models.py 迁入，内容不变）
- [x] 1.3 创建 noteforge/infra/file_io.py（合并 file_utils.py + 各模块 _read_file/_write_file 的最佳实现）
- [x] 1.4 创建 noteforge/infra/logging_setup.py（从 logging_config.py 迁入）
- [x] 1.5 创建 noteforge/infra/colors.py（从 ansi_colors.py 迁入）
- [x] 1.6 创建 noteforge/infra/env.py（从 env_check.py 迁入）
- [x] 1.7 创建 noteforge/__init__.py（版本 + 顶层 re-export）
- [x] 1.8 创建 scripts/_compat.py 兼容层
- [x] 1.9 删除 topic_classifier.py（零引用死代码）
- [x] 1.10 验证：python -m compileall noteforge scripts + 189 测试通过

### 验收标准
- `python -c "from noteforge.infra.file_io import read_file, write_file"` 正常
- `python -m compileall -q noteforge scripts` 0 error
- `pytest tests/ -v` 全部通过

---

## Commit 2：模块迁移 + import 路径替换（中风险，~2h）

### 目标
- 所有模块迁移到 noteforge/ 对应子包
- 替换 sys.path.insert + 绝对 import 为相对 import
- 替换 6 处 _read_file → infra.file_io.read_file
- 替换 2 处 _write_file → infra.file_io.write_file
- 更新 noteforge.bat 入口

### 步骤
- [x] 2.1 迁移 core/ 模块（llm_providers/prompt_builder/note_formatter/token_manager/transcript_preprocessor/domain_classifier）
- [x] 2.2 迁移 sources/ 模块（youtube_handler→youtube/bilibili_download→bilibili/podcast_handler→podcast）
- [x] 2.3 迁移 quality/ 模块（quality_gate→gate/quality_manager→manager/batch_quality→batch）
- [x] 2.4 迁移 intelligence/ 模块（synthesis_engine→synthesis/knowledge_index→knowledge_index）
- [x] 2.5 迁移 integration/ 模块（feishu_client→feishu/external_sync→sync）
- [x] 2.6 迁移 engine/ 模块（llm_note_engine→note_engine）
- [x] 2.7 迁移 batch/ 模块（batch_processor→processor）
- [x] 2.8 迁移 cli/ 模块（cli→main）
- [x] 2.9 替换所有内部 import 为相对 import
- [x] 2.10 移除 5 处 sys.path.insert(0, ...)
- [x] 2.11 替换 6 处 _read_file → from ..infra.file_io import read_file
- [x] 2.12 替换 2 处 _write_file → from ..infra.file_io import write_file
- [x] 2.13 更新 scripts/_compat.py 兼容层（旧路径 re-export）
- [x] 2.14 更新 noteforge.bat 入口指向 noteforge.cli.main
- [x] 2.15 更新测试文件 import 路径
- [x] 2.16 验证：compileall + 189 测试通过 + --help 冒烟

### 验收标准
- 所有模块在新位置
- `grep -r "sys.path.insert" noteforge/` 返回 0 结果
- `grep -r "_read_file\|_write_file" noteforge/` 返回 0 结果（已统一到 file_io）
- 旧导入路径通过兼容层仍可用
- `noteforge.bat` 菜单正常

---

## Commit 3：PipelineContext + CLI 解耦 + Source 接口（中风险，~2h）

### 目标
- 创建 PipelineContext dataclass
- CLI 用 app.configure() 替代 engine._xxx 赋值
- 提取 Source ABC + SourceRegistry
- 提取 MediaDownloader 到 sources/downloader.py
- 提取 QualityMetrics 到 quality/heuristics.py

### 步骤
- [x] 3.1 创建 noteforge/context.py — PipelineContext dataclass
- [x] 3.2 engine/note_engine.py 添加 configure() 方法（content_type/paths 同步到子系统）
- [x] 3.3 重构 cli/main.py：用 app.configure() 替代 engine._xxx = ...
- [x] 3.4 创建 sources/base.py — Source ABC + FetchResult + SourceRegistry
- [x] 3.5 提取 cli.py 的 MediaDownloader → sources/downloader.py
- [x] 3.6 提取 quality_gate.py 的 QualityMetrics + 6 个启发式计算 → quality/heuristics.py
- [x] 3.7 验证：189 测试通过 + configure() 正常

### 验收标准
- `grep -r "engine\._\|app\._" noteforge/cli/` 返回 0 结果
- `python scripts/cli.py --input ep01` 正常
- `python scripts/cli.py --check-only output/notes/xxx.md` 正常
- Source 接口可注册新数据源

---

## Commit 4：engine 瘦身 — 提取 generate 阶段（中高风险，~3h）

### 目标
- 从 note_engine.py 提取质量反馈循环 + 分块生成到 stages/generate.py
- 创建 pipeline.py 编排器
- note_engine.py 委托给 pipeline 执行
- 保留 LLMNoteEngine 兼容接口

### 步骤
- [x] 4.1 创建 engine/pipeline.py — Pipeline 编排器
- [x] 4.2 创建 engine/stages/generate.py — 提取 _generate_with_quality_loop + _generate_chunked + _generate_chunk_summary + _merge_chunk_notes
- [x] 4.3 note_engine.py generate_note() 委托给 pipeline
- [x] 4.4 保留 LLMNoteEngine 兼容接口（旧代码仍可 import）
- [x] 4.5 验证：189 测试通过 + engine 从 812 行降至 642 行

### 验收标准
- 单文件生成结果与重构前一致
- 批量生成结果一致
- `from noteforge.engine import LLMNoteEngine` 仍可用

---

## 后续路线（本次不做）

| 阶段 | 内容 | 前置 |
|------|------|------|
| 阶段 2 | 继续提取 pipeline stages（ingest/transcribe/preprocess/format/evaluate/persist） | Commit 4 完成 |
| 阶段 3 | quality_gate.py 深度拆分（gate/report/cli 三模块） | 阶段 2 完成 |
| 阶段 4 | 测试补充（64 → 120+） | 阶段 2 完成 |
| 阶段 5 | 删除 scripts/ 兼容层 | 阶段 3 完成，确认无外部依赖 |

---

## 回滚策略

每个 Commit 独立可回滚：
- Commit 1：删除 noteforge/ 目录，恢复 topic_classifier.py
- Commit 2：git revert，scripts/ 旧文件仍在
- Commit 3：git revert，PipelineContext 是新增文件不影响旧代码
- Commit 4：git revert，note_engine.py 原逻辑仍在
