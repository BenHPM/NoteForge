# NoteForge — 智能笔记锻造系统

视频/音频/播客 → ASR 转录 → LLM 笔记生成 → 12 维质量门禁 → 知识合成（域隔离） → 飞书同步

## 项目结构

```
NoteForge/
  scripts/feishu_sync.py              # 飞书同步入口（薄封装）
  video-to-text/
    noteforge.bat                     # 主 CLI 菜单（23 选项）
    compare_notes.py                  # 笔记版本对比测试工具
    config/
      llm_engine_config.yaml          # LLM/质量/路径/飞书/知识域 配置
      note_generation_rules.yaml      # R1-R12 硬规则 + 领域概念配置
      experience_log.yaml             # 历史错误教训（16 条）
      video-mapping.json              # 集数 ID→标题映射
      podcast_feeds.json              # Podcast RSS 订阅
    scripts/
      llm_note_engine.py              # 核心引擎（笔记生成 + 合成 + 增量更新）
      llm_providers.py                # LLM 抽象层（Claude/OpenAI，含 Prompt Caching）
      prompt_builder.py               # Prompt 组装（content_type 感知，4 种类型）
      note_formatter.py               # 笔记后处理（content_type 感知 + 转写质量声明）
      transcript_preprocessor.py      # 文本清洗/去语气词/token 计数/分块
      quality_gate.py                 # R0-R12 质量评分引擎 + 启发式指标
      token_manager.py                # Token 使用追踪 + 成本预估
      batch_quality.py                # 批量质量评分
      paraformer_transcribe.py        # FunASR Paraformer ASR 转录
      youtube_handler.py              # yt-dlp YouTube 下载
      bilibili_download.py            # B站双策略下载（yt-dlp + API 降级）
      podcast_handler.py              # Podcast RSS 订阅管理
      feishu_client.py                # 飞书 Wiki API 客户端
      knowledge_index.py              # jieba + TF-IDF 笔记搜索/标签
      topic_classifier.py             # 主题分类（未深度使用）
      env_check.py                    # 运行时环境检测
    tests/
      test_pipeline.py                # 核心流水线单元测试（23 个）
    output/
      transcripts/                    # ASR 转录文本
      notes/                          # 生成的 Markdown 笔记
      notes/extractions/              # 逐集概念提取结果（两阶段合成缓存）
      quality_reports/                # 质量报告 JSON
      logs/                           # noteforge.log + token_usage_*.json
      audio/                          # 下载的音频（gitignored）
    envs/paraformer/                  # Python 3.10 隔离环境（FunASR + torch）
```

## 核心流水线

```
源（YouTube/B站/音频平台/Podcast/本地文件）
  → 下载/提取音频（yt-dlp / bilibili_download / ffmpeg）
  → ASR 转录（Paraformer + VAD + 标点 + 说话人识别）
  → 文本预处理（去噪/去语气词/连续标点规范化/token 计数/超长分块）
  → LLM 生成（Claude Sonnet，注入 R1-R12 规则 + 历史教训，content_type 自适应）
  → 质量反馈循环（不达标 → 带问题反馈重试，最多 3 次）
  → 格式化 + 质量门禁（R0-R12 加权评分 + 启发式指标 + 转写质量声明）
  → 可选：飞书 Wiki 同步
  → 可选：知识合成（域隔离，两阶段 + 矛盾检测）
```

## 运行环境

### 首次安装（新机器）

```powershell
# 1. 安装 Python 3.10（必须是 3.10，FunASR 依赖）
winget install Python.Python.3.10

# 2. 创建隔离环境 + 安装依赖
cd video-to-text
py -3.10 -m venv envs/paraformer
envs\paraformer\Scripts\activate
pip install -r requirements.txt

# 3. 安装系统工具
pip install yt-dlp
winget install ffmpeg

# 4. 配置环境变量（复制 .env.example 为 .env 并填入）
cp ..\.env.example ..\.env
```

### 运行时依赖

- **ASR 环境**：`video-to-text/envs/paraformer/python.exe`（Python 3.10 + FunASR + torch）
- **LLM**：默认直连 `https://api.anthropic.com`，需设置 `ANTHROPIC_API_KEY` 环境变量
  - 如需代理：在 `llm_engine_config.yaml` 中取消 `base_url` 注释
  - 代理不可达时自动降级到直连
  - 支持 Anthropic Prompt Caching（system prompt 自动缓存，批量生成节省 12%+）
- **yt-dlp**：下载 YouTube/B站/音频平台音频，需在 PATH 中
- **ffmpeg**：视频提取音频，需在 PATH 中
- **lark-cli**（可选）：飞书同步需要，`npm install -g @anthropic-ai/lark-cli`

### 环境变量（`.env`）

| 变量 | 必填 | 说明 |
|------|------|------|
| `ANTHROPIC_API_KEY` | 是（用 Claude 时） | Anthropic API 密钥（cc-switch 统一管理） |
| `OPENAI_API_KEY` | 否 | OpenAI API 密钥（切换 provider 时需要） |
| `FEISHU_APP_ID` | 否 | 飞书应用 ID（lark-cli 认证用，非代码直接读取） |
| `FEISHU_APP_SECRET` | 否 | 飞书应用密钥（lark-cli 认证用，非代码直接读取） |

## 常用命令

```bash
cd video-to-text
PY=envs/paraformer/python.exe

# 笔记生成
$PY scripts/llm_note_engine.py --input ep01
$PY scripts/llm_note_engine.py --input ep01 --content-type lecture  # 指定内容类型
$PY scripts/llm_note_engine.py --batch --skip-existing

# 质量检查
$PY scripts/llm_note_engine.py --check-only output/notes/xxx.md

# 知识合成（四种模式）
$PY scripts/llm_note_engine.py --mode synthesis              # 单次合成
$PY scripts/llm_note_engine.py --mode synthesis-2stage        # 两阶段（推荐）
$PY scripts/llm_note_engine.py --mode synthesis-incremental --input notes/xxx.md
$PY scripts/llm_note_engine.py --mode meeting                 # 会议纪要

# 笔记版本对比
$PY compare_notes.py <source.txt> <note_v1.md> <note_v2.md> --rules config/note_generation_rules.yaml --content-type interview

# 运行测试
$PY -m pytest tests/ -v
```

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

## Prompt 策略（v2.0 content_type 感知）

| 内容类型 | 角色 | 格式重点 |
|---------|------|---------|
| `lecture` | 知识提炼专家 | 观点提炼 + 知识框架 + 可迁移洞察 |
| `tutorial` | 课程笔记整理专家 | 操作步骤 + 工具使用 + 实战经验 |
| `interview` | 访谈结构化整理专家 | 区分主持人/嘉宾 + 原话保留 |
| `podcast` | 播客内容整理专家 | 区分发言者 + 核心话题提炼 |
| `meeting` | 会议纪要整理专家 | 议题追踪 + 决策记录 + 行动项 |

### 自检清单（16 项）
- 忠实性 5 项（数字/覆盖/术语/语义/补充标记）
- 知识提炼 3 项（框架/洞察/分层）
- 可读性 5 项（保留原话/段落长度/行动具体性/引用准确性/信息密度）
- 忠实度护栏 3 项（转写模糊处理/人名数字校对/转写质量声明）

## 知识合成（域隔离）

### 知识域配置（`llm_engine_config.yaml` → `knowledge_domains`）

```
同一域 → 跨集合成（关联发现 + 矛盾检测）
不同域 → 各自独立合成（互不干扰）
```

| 域 ID | 匹配关键词 | 排除词 | 匹配文件 |
|--------|-----------|--------|---------|
| short_video_directing | 导演/短视频/拍摄/剪辑/剧本/创作流程/爆火/IP/镜头/运镜/文案/封面 | 量化/投资/基金/地缘/中美 | 第*集*/ep0*/ep1* |
| finance_investment | 量化/基金/因子/ROE/换手/超额/胜率/算力/T0/黄金/美联储/华尔街/道琼斯/金融/股价/回撤 | 导演/短视频/拍摄 | *量化*/*基金*/*黄金* |
| geoeconomics | 地缘/制裁/贸易战/关税/能源/石油/冲突/战争/脱钩/供应链/稀土/芯片战/科技战 | 翟东升/政经启翟/特朗普/中美/博弈/导演/短视频/拍摄/剪辑 | *地缘*/*制裁*/*贸易战*/*冲突* |
| intl_analysis | 国际/美国/欧洲/全球化/格局/秩序/霸权/外交/联盟/G7/BRICS/联合国/北约/中东/印度/莫迪/俄罗斯 | — | *国际*/*美国*/*格局*/*印度* |
| china_politics | 中国/房产/内需/消费/产业升级/改革/政策/央行/GDP/通胀/利率/货币/人民币/财政/共同富裕/人口/制造业 | — | *中国*/*房产*/*经济*/*共同富裕* |
| geopolitics | 中美/博弈/翟东升/缠斗/货币/美元/美债/政经启翟/特朗普/美帝国/启翟 | 制裁/贸易战/关税/供应链/芯片战 | *翟东升*/*正在发生*/*中美*/*政经启翟* |
| general | 兜底（无关键词） | — | * |

**新增领域**：在 `knowledge_domains` 追加一条即可，不用改代码。

### 三种合成模式

| 模式 | 命令 | 适用场景 |
|------|------|---------|
| 单次合成 | `--mode synthesis` | 快速，≤10 篇笔记 |
| 两阶段合成 | `--mode synthesis-2stage` | 推荐，逐集提取+矛盾检测+域隔离 |
| 增量更新 | `--mode synthesis-incremental --input xxx.md` | 新增 1 篇同域笔记 |
| 会议纪要 | `--mode meeting` | 音频/视频会议，区分议题+决策+行动项 |

### 推荐流程

```
首次建库: 两阶段合成 → 矛盾检测 → 知识体系文档
日常迭代: 增量更新（域校验 → 提取 → 更新同域文档）
定期审查: 每 5 集全量重建（保证深层关联不遗漏）
```

## Token 管理

- 每次 LLM 调用自动记录 input/output/cached tokens
- 支持 Anthropic Prompt Caching（system prompt 缓存，后续调用只付 10%）
- 日志持久化到 `output/logs/token_usage_*.json`
- 预估命令：`token_manager.estimate_episode_cost(transcript_chars)`
- 批量生成后自动打印成本统计和缓存命中率

## 开发约定

- **Python 版本**：隔离环境是 3.10，顶层脚本兼容 3.10+
- **编码**：所有文件 UTF-8，脚本头部 `# -*- coding: utf-8 -*-`
- **LLM 调用**：通过 `llm_providers.py` 抽象层，不要直接调 requests
- **LLM API**：100% 在线 API，不使用本地小模型
- **质量规则**：R1/R2/R3/R5 是致命规则（R5 仅在覆盖率 <30% 时为 fatal），单项不通过即 overall 不通过
- **日志**：控制台 + `output/logs/noteforge.log` 双写
- **飞书分类**：`feishu_client.match_category()` 支持 `match` 列表格式（扁平匹配）

## 飞书知识库分类准则（稳定结构，勿随意修改）

```
AI笔记库 (root_node_token，固定不变)
  ├── {二级分类A}     ← 按 match 关键词自动归类（对应知识域）
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

**排序**：跨集提炼在上（高层知识），逐集笔记在下（原始素材），其他笔记始终最后

## 已知限制

- 小宇宙/荔枝FM 的 API 是未公开接口，可能随平台更新变化
- 喜马拉雅仅 yt-dlp 单策略，无 API 降级；`/album/` 链接不支持
- DRM 平台（Spotify/Apple Music/网易云/QQ 音乐）无法提取音频
- 微信视频号因封闭生态无法自动化支持
