# NoteForge

> **智能笔记锻造系统** — 将视频/播客/音频内容自动转写并提炼为高质量学习笔记，支持一键同步到飞书知识库

[![Status](https://img.shields.io/badge/status-Ready-brightgreen)](https://github.com/BenHPM/NoteForge)
[![Engine](https://img.shields.io/badge/engine-Paraformer%20(FunASR)-blue)](https://github.com/modelscope/FunASR)
[![LLM](https://img.shields.io/badge/LLM-Claude%20%2F%20OpenAI%20%2F%20Local-yellow)](#)
[![Feishu](https://img.shields.io/badge/飞书-知识库同步-blueviolet)](#飞书知识库同步)

---

## 核心特性

- 🎬 **视频转笔记**: Paraformer ASR (96%+ 准确率) + LLM 知识提炼，一键完成
- 🧠 **知识提炼**: 不只是整理原文，提取可迁移的框架、思维模型和行动方法论
- 📺 **YouTube / B站**: 输入 URL，自动下载音频 + 转写 + 笔记
- 🎙️ **播客 RSS**: 订阅 podcast，自动下载、转写、生成笔记
- 🛡️ **12 维质量门禁 (R0-R12)**: 自动评分 + 反馈重试循环，确保笔记准确有深度
- 📊 **知识合成**: 跨集知识框架、思维模型图谱、行动手册自动生成（域隔离）
- 🔍 **笔记搜索**: 关键词搜索 + 标签提取 + 历史笔记关联
- 📚 **飞书知识库同步**: 笔记一键同步到飞书 Wiki，支持增量更新和分类组织
- ⚡ **批处理**: 无人值守自动流水线，断点续传，8-12h 无人值守

---

## 快速开始

### 方法一：交互式 CLI 菜单（推荐）

```
双击: video-to-text\noteforge.bat
```

### 方法二：命令行

```powershell
# 统一入口（所有功能通过 python -m noteforge 访问）
$py = "video-to-text\envs\paraformer\python.exe"

# 笔记生成（完整流程：ASR → 预处理 → LLM → 质量门禁）
& $py -m noteforge --input ep01
& $py -m noteforge --input ep01 --content-type lecture  # 指定内容类型
& $py -m noteforge --input ep01 --force                  # 覆盖已有笔记

# 批量生成（跳过已存在的）
& $py -m noteforge --batch --skip-existing

# 仅质量检查
& $py -m noteforge --check-only output/notes/xxx.md

# 知识合成
& $py -m noteforge --mode synthesis              # 单次合成
& $py -m noteforge --mode synthesis-2stage        # 两阶段（推荐）
& $py -m noteforge --mode synthesis-incremental --input notes/xxx.md

# ASR 转写（直接调用 FunASR）
& $py -m noteforge.sources.asr ep01

# 飞书同步
& $py -m noteforge.integration.feishu_sync
& $py -m noteforge.integration.feishu_sync --dry-run      # 预览
& $py -m noteforge.integration.feishu_sync --new-only     # 仅新增
& $py -m noteforge.integration.feishu_sync --file "关键词" # 单文件
```

---

## 项目结构

```
NoteForge/
├── README.md                         # 本文件
├── CLAUDE.md                         # AI 辅助开发指南
├── .env.example                      # 环境变量模板
├── .env                              # 实际环境变量（.gitignore）
│
├── video-to-text/                    # 核心应用
│   ├── pyproject.toml                # Python 包配置
│   ├── noteforge.bat                 # Windows CLI 菜单（23 选项）
│   │
│   ├── config/                       # 配置文件（不修改代码即可适配）
│   │   ├── llm_engine_config.yaml    # LLM/质量/路径/飞书/知识域 配置
│   │   ├── note_generation_rules.yaml # R1-R12 硬规则 + 领域概念配置
│   │   ├── experience_log.yaml       # 历史错误教训（注入 prompt）
│   │   ├── video-mapping.json        # 集数 ID→标题映射
│   │   ├── podcast_feeds.json        # Podcast RSS 订阅
│   │   └── format_templates.yaml     # 输出格式模板
│   │
│   ├── noteforge/                    # 主包（v5.0 架构重构）
│   │   ├── __init__.py               # 版本号 + 顶层 re-export
│   │   ├── __main__.py               # python -m noteforge 入口
│   │   ├── models.py                 # GenerationResult, TokenUsage
│   │   │
│   │   ├── infra/                    # 基础设施（稳定底层）
│   │   │   ├── file_io.py            # 统一 read_file / write_file
│   │   │   ├── logging_setup.py      # 日志配置
│   │   │   ├── colors.py             # ANSI 颜色常量
│   │   │   └── env.py                # 运行时环境检测
│   │   │
│   │   ├── core/                     # 领域核心
│   │   │   ├── llm_providers.py      # LLM 抽象层（Claude/OpenAI/Local + Prompt Caching）
│   │   │   ├── prompt_builder.py     # Prompt 组装（content_type 感知）
│   │   │   ├── note_formatter.py     # 笔记后处理（content_type 感知 + 转写质量声明）
│   │   │   ├── token_manager.py      # Token 使用追踪 + 成本预估
│   │   │   ├── transcript_preprocessor.py # 文本清洗/去语气词/token 计数/分块
│   │   │   ├── domain_classifier.py  # 知识域分类（文件名匹配 + 关键词加权）
│   │   │   └── audio_handler.py      # ASR 转写 + 标题提取 + 转写定位（ffprobe 时长检测）
│   │   │
│   │   ├── sources/                  # 数据源（变化最快）
│   │   │   ├── base.py               # Source ABC + FetchResult + SourceRegistry
│   │   │   ├── youtube.py            # yt-dlp YouTube 下载
│   │   │   ├── bilibili.py           # B站双策略下载（yt-dlp + API 降级）
│   │   │   ├── podcast.py            # Podcast RSS 订阅管理
│   │   │   ├── rss_parser.py         # RSS feed 解析
│   │   │   ├── downloader.py         # MediaDownloader（多平台降级）
│   │   │   └── asr.py                # FunASR Paraformer ASR 转录
│   │   │
│   │   ├── quality/                  # 质量系统
│   │   │   ├── gate.py               # QualityGate 评分引擎（R0-R12 + 启发式）
│   │   │   ├── manager.py            # 质量门禁评估 + 报告
│   │   │   ├── rules.py              # R8-R12 规则检查
│   │   │   ├── rules_factual.py      # R1-R3 规则检查（虚构/越界/反转）
│   │   │   ├── rules_coverage.py     # R4-R7 规则检查（失真/覆盖/术语/框架）
│   │   │   └── heuristics.py         # 启发式指标（压缩比/结构丰富度/信息密度）
│   │   │
│   │   ├── intelligence/             # LLM + 合成
│   │   │   ├── synthesis.py          # 知识合成（单次/两阶段/增量）
│   │   │   ├── knowledge_index.py    # jieba + TF-IDF 笔记搜索/标签
│   │   │   ├── prompts.py            # 合成 prompt 模板
│   │   │   └── validation.py         # 合成结果验证
│   │   │
│   │   ├── integration/              # 外部集成
│   │   │   ├── feishu.py             # 飞书 Wiki API 客户端（lark-cli）
│   │   │   ├── feishu_sync.py        # 飞书批量同步 CLI（扫描+哈希缓存+分类+清理）
│   │   │   └── sync.py               # 飞书同步 + 关联上下文
│   │   │
│   │   ├── engine/                   # 编排层
│   │   │   ├── note_engine.py        # LLMNoteEngine 核心引擎（Pipeline 编排）
│   │   │   ├── pipeline.py           # Pipeline 编排器（阶段排序 + 错误处理）
│   │   │   └── stages/               # Pipeline 阶段
│   │   │       ├── base.py           # PipelineStage 基类
│   │   │       ├── preprocess.py     # 文本预处理 + 分块 + 上下文注入
│   │   │       ├── generate.py       # 质量反馈循环 + 分块生成
│   │   │       ├── format.py         # 格式化 + 结构校验
│   │   │       ├── save.py           # 保存笔记 + 中文名副本
│   │   │       └── evaluate.py       # 质量门禁评估 + 报告保存
│   │   │
│   │   ├── batch/                    # 批量处理
│   │   │   ├── processor.py          # 批量编排 + 摘要
│   │   │   ├── auto_pipeline.py      # 自主执行流水线（8-12h 无人值守，断点续传）
│   │   │   └── bilibili.py           # B站批量处理（进度追踪+断点续传+dry-run）
│   │   │
│   │   └── cli/                      # CLI 入口
│   │       ├── main.py               # CLI 参数解析 + 主流程
│   │       └── commands/             # 模式执行逻辑
│   │           ├── sources.py        # 源获取命令（YouTube/B站/Podcast）
│   │           └── generate.py       # 笔记生成 + 合成命令
│   │
│   ├── envs/paraformer/              # Python 3.10 隔离环境（FunASR + torch）
│   └── output/                       # 输出目录（.gitignore）
│       ├── notes/                    # 生成的 Markdown 笔记
│       ├── transcripts/              # ASR 转录文本
│       ├── quality_reports/          # 质量评估报告 JSON
│       ├── logs/                     # noteforge.log + token_usage_*.json
│       └── audio/                    # 下载的音频
│
└── tests/                            # 测试套件（425+ 测试，全通过）
```

---

## 流水线架构

```
源（YouTube/B站/播客/本地文件）
  → 下载/提取音频（yt-dlp / ffmpeg）
  → ASR 转录（Paraformer + VAD + 标点 + 说话人识别，ffprobe 时长检测）
  → 文本预处理（去噪/去语气词/连续标点规范化/token 计数/超长分块）
  → LLM 生成（Claude Sonnet，注入 R1-R12 规则 + 历史教训，content_type 自适应）
  → 质量反馈循环（不达标 → 带问题反馈重试，最多 3 次）
  → 格式化 + 质量门禁（R0-R12 加权评分 + 启发式指标 + 转写质量声明）
  → 可选：飞书 Wiki 同步
  → 可选：知识合成（域隔离，两阶段 + 矛盾检测）
```

### Pipeline 阶段

| 阶段 | 功能 |
|------|------|
| `preprocess` | 文本清洗 + 分块 + 上下文注入 |
| `generate` | 质量反馈循环 + 分块 LLM 生成（超长文本自动摘要合并） |
| `format` | Markdown 格式化 + 结构校验 |
| `save` | 保存笔记 + 中文名副本 |
| `quality_gate` | R0-R12 评分 + 启发式指标 |
| `postprocess` | Token 统计 + 飞书同步 + 自动合成 |

### 内容类型

| 内容类型 | 角色 | 格式重点 |
|---------|------|---------|
| `lecture` | 知识提炼专家 | 观点提炼 + 知识框架 + 可迁移洞察 |
| `tutorial` | 课程笔记整理专家 | 操作步骤 + 工具使用 + 实战经验 |
| `interview` | 访谈结构化整理专家 | 区分主持人/嘉宾 + 原话保留 |
| `podcast` | 播客内容整理专家 | 区分发言者 + 核心话题提炼 |
| `meeting` | 会议纪要整理专家 | 议题追踪 + 决策记录 + 行动项 |

---

## 质量门禁（R0-R12）

| 规则 | 严重度 | 说明 |
|------|--------|------|
| R0 | baseline | 内容完整性（<200 字直接不通过） |
| R1 | fatal | 禁止虚构数据（数字/百分比必须有原文出处） |
| R2 | fatal | 禁止越界增补（补充内容须标注 [📝笔者补充]） |
| R3 | fatal | 禁止事实反转（语义方向不得与原文相反） |
| R4 | major | 禁止概念简化失真（按领域加载 KEY_CONCEPTS） |
| R5 | fatal/major | 覆盖度底线（双阈值：<30% fatal，<80% major） |
| R6 | medium | 术语一致性（术语表与正文不矛盾） |
| R7 | major | 框架完整性（步骤不得因简化丢失） |
| R8 | major | 洞察可行动性（必须有具体行动指引） |
| R9 | medium | 分层准确性（区分引用原话 vs 过度泛化） |
| R10 | medium | 时间线准确性（不得虚构时序关系） |
| R11 | major | 引用归属（人名归属正确，含 ASR 同音字模糊匹配） |
| R12 | medium | 人名/数字一致性（笔记中的人名数字须与转写对应） |

### 启发式质量指标（零 API 成本）

| 指标 | 说明 | 理想范围 |
|------|------|---------|
| 压缩比 | 笔记/原文字数比 | 10-30% |
| 结构丰富度 | 标题+列表+表格+引用 | ≥70% |
| 信息密度 | 概念多样性/句数 | ≥80% |
| 可读性 | 段落质量+结构多样性 | ≥70% |
| 原话引用比 | 引用句/总行数 | ≥10% |
| 行动具体性 | 可执行行动项/总行动项 | ≥50% |

---

## 知识合成（域隔离）

### 知识域配置（`llm_engine_config.yaml` → `knowledge_domains`）

```
同一域 → 跨集合成（关联发现 + 矛盾检测）
不同域 → 各自独立合成（互不干扰）
```

| 域 ID | 匹配关键词 | 匹配文件 |
|--------|-----------|---------|
| short_video_directing | 导演/短视频/拍摄/剪辑/剧本/创作流程 | 第*集*/ep0* |
| finance_investment | 量化/基金/因子/ROE/换手/超额/胜率 | *量化*/*基金* |
| geoeconomics | 地缘/制裁/贸易战/关税/能源/石油/冲突 | *地缘*/*制裁* |
| intl_analysis | 国际/美国/欧洲/全球化/格局/秩序/霸权 | *国际*/*格局* |
| china_politics | 中国/房产/内需/消费/产业升级/改革/政策 | *中国*/*房产* |
| geopolitics | 中美/博弈/翟东升/特朗普/政经启翟 | *翟东升*/*中美* |
| general | 兜底（无关键词） | * |

**新增领域**：在 `knowledge_domains` 追加一条即可，不用改代码。

### 三种合成模式

| 模式 | 命令 | 适用场景 |
|------|------|---------|
| 单次合成 | `--mode synthesis` | 快速，≤10 篇笔记 |
| 两阶段合成 | `--mode synthesis-2stage` | 推荐，逐集提取 + 矛盾检测 + 域隔离 |
| 增量更新 | `--mode synthesis-incremental --input notes/xxx.md` | 新增 1 篇同域笔记 |
| 会议纪要 | `--mode meeting` | 音频/视频会议，区分议题 + 决策 + 行动项 |

---

## 飞书知识库同步

### 分类结构

```
AI笔记库 (root_node_token，固定不变)
  ├── {二级分类A}     ← 按 match 关键词自动归类
  │     ├── 跨集提炼  ← 知识体系/框架/模型，随新笔记迭代丰富
  │     └── 逐集笔记  ← 每篇独立笔记，持续增长
  ├── {二级分类B}
  │     ├── 跨集提炼
  │     └── 逐集笔记
  └── 其他笔记        ← 暂存池，无子结构，平铺
```

**规则**：
1. 根节点「AI笔记库」固定不变，永远不重建
2. 二级分类按 `match` 关键词自动匹配文件名，第一个命中生效
3. 普通二级分类内部固定有「跨集提炼」（在上）和「逐集笔记」（在下）
4. 「其他笔记」是暂存池，无子结构；同类笔记积累到一定量后独立出新二级分类
5. 新增主题只需在 `llm_engine_config.yaml` 的 `categories` 加一条 match 规则

### 同步策略

| 模式 | 命令 | 说明 |
|------|------|------|
| 全量同步 | `python -m noteforge.integration.feishu_sync` | 扫描所有笔记，已存在的更新内容 |
| 仅新增 | `--new-only` | 跳过已存在的节点，只同步新增笔记 |
| 预览模式 | `--dry-run` | 打印同步计划，不执行 API 调用 |
| 单文件 | `--file "关键词"` | 只同步匹配关键词的文件 |
| 单分类 | `--category "分类名"` | 只同步指定分类 |
| 清理重建 | `--clean --clean-confirm` | 删除飞书所有内容后重新同步 |

### 前置条件

1. **安装 lark-cli**: `npm install -g @anthropic-ai/lark-cli`
2. **认证**: `lark-cli auth login` — 完成飞书 OAuth 授权（长期有效）
3. **应用权限**: 飞书应用需要 `wiki:wiki` + `docx:document` 权限

---

## 运行环境

### 首次安装（新机器）

```powershell
# 1. 安装 Python 3.10（FunASR 依赖）
winget install Python.Python.3.10

# 2. 创建隔离环境 + 安装依赖
cd video-to-text
py -3.10 -m venv envs/paraformer
envs\paraformer\Scripts\activate
pip install -r requirements.txt

# 3. 安装系统工具
pip install yt-dlp
winget install ffmpeg

# 4. 配置环境变量
cp ..\.env.example ..\.env
```

### 运行时依赖

| 依赖 | 说明 |
|------|------|
| **ASR 环境** | `video-to-text/envs/paraformer/python.exe`（Python 3.10 + FunASR + torch） |
| **LLM** | 默认直连 `https://api.anthropic.com`，需 `ANTHROPIC_API_KEY` |
| **yt-dlp** | YouTube/B站 音频下载 |
| **ffmpeg** | 视频提取音频 |
| **lark-cli** | 飞书同步（可选） |

---

## 性能参考

| 视频时长 | 转写耗时 | 笔记生成 | 总耗时 |
|---------|---------|---------|--------|
| 10 分钟 | ~2 分钟 | ~80 秒 | ~4 分钟 |
| 70 分钟 | ~15 分钟 | ~90 秒 | ~18 分钟 |
| 4 小时 | ~80 分钟 | ~3 分钟 | ~85 分钟 |

---

## 许可证

本项目仅供个人学习研究使用。

---

**最后更新**: 2026-07-28
**版本**: v5.1.0 (Pipeline Architecture + 12 Quality Rules + Feishu Sync)
