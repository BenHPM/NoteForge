# NoteForge — 智能笔记锻造系统

视频/音频/播客 → ASR 转录 → LLM 笔记生成 → 9 维质量门禁 → 飞书同步

## 项目结构

```
NoteForge/
  scripts/feishu_sync.py              # 飞书同步入口（薄封装）
  video-to-text/
    noteforge.bat                     # 主 CLI 菜单（20 选项）
    config/
      llm_engine_config.yaml          # LLM/质量/路径/飞书 配置
      note_generation_rules.yaml      # R1-R9 硬规则
      experience_log.yaml             # 历史错误教训
      video-mapping.json              # 集数 ID→标题映射
      podcast_feeds.json              # Podcast RSS 订阅
    scripts/
      llm_note_engine.py              # 核心引擎（CLI 入口 + 笔记生成 + 质量循环）
      llm_providers.py                # LLM 抽象层（Claude/OpenAI/Local）
      prompt_builder.py               # Prompt 组装（system/user/feedback）
      note_formatter.py               # 笔记后处理（标题/定位/页脚）
      transcript_preprocessor.py      # 文本清洗/去语气词/token 计数/分块
      quality_gate.py                 # R0-R9 质量评分引擎
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
      quality_reports/                # 质量报告 JSON
      logs/                           # noteforge.log 持久化日志
      audio/                          # 下载的音频（gitignored）
    envs/paraformer/                  # Python 3.10 隔离环境（FunASR + torch）
```

## 核心流水线

```
源（YouTube/B站/音频平台/Podcast/本地文件）
  → 下载/提取音频（yt-dlp / bilibili_download / ffmpeg）
  → ASR 转录（Paraformer + VAD + 标点 + 说话人识别）
  → 文本预处理（去噪/去语气词/token 计数/超长分块）
  → LLM 生成（Claude/OpenAI/Local，注入 R1-R9 规则 + 历史教训）
  → 质量反馈循环（不达标 → 带问题反馈重试，最多 3 次）
  → 格式化 + 质量门禁（R0-R9 加权评分，≥0.80 且致命规则全过）
  → 可选：飞书 Wiki 同步
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
# GPU 用户：替换 torch 为 CUDA 版本，见 https://pytorch.org/get-started/locally/

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
- **yt-dlp**：下载 YouTube/B站/音频平台音频，需在 PATH 中
- **ffmpeg**：视频提取音频，需在 PATH 中
- **lark-cli**（可选）：飞书同步需要，`npm install -g @anthropic-ai/lark-cli`

### 环境变量（`.env`）

| 变量 | 必填 | 说明 |
|------|------|------|
| `ANTHROPIC_API_KEY` | 是（用 Claude 时） | Anthropic API 密钥 |
| `OPENAI_API_KEY` | 否 | OpenAI API 密钥（切换 provider 时需要） |
| `FEISHU_APP_ID` | 否 | 飞书应用 ID（仅飞书同步） |
| `FEISHU_APP_SECRET` | 否 | 飞书应用密钥（仅飞书同步） |

## 常用命令

```bash
# 进入项目目录
cd video-to-text

# 使用隔离环境运行（ASR 相关必须）
PY=envs/paraformer/python.exe

# 本地视频一键流程
$PY scripts/llm_note_engine.py --input video.mp4

# YouTube
$PY scripts/llm_note_engine.py --youtube "https://youtube.com/watch?v=xxx"

# B站（双策略，无需 Cookie）
$PY scripts/llm_note_engine.py --bilibili "BV1xxx"

# 音频平台（小宇宙/喜马拉雅/荔枝FM/微博/抖音 等）
$PY scripts/llm_note_engine.py --audio-url "https://..."

# 批量处理
$PY scripts/llm_note_engine.py --batch --skip-existing

# 仅质量检查
$PY scripts/llm_note_engine.py --check-only output/notes/xxx.md

# 运行测试
$PY -m pytest tests/ -v
```

## 开发约定

- **Python 版本**：隔离环境是 3.10，顶层脚本兼容 3.10+
- **编码**：所有文件 UTF-8，脚本头部 `# -*- coding: utf-8 -*-`
- **LLM 调用**：通过 `llm_providers.py` 抽象层，不要直接调 requests
- **新增平台下载器**：参考 `bilibili_download.py` 的 `download_bilibili()` 接口（返回 `{success, path, title, duration, method}`）
- **质量规则**：R1/R2/R3/R5 是致命规则，单项不通过即 overall 不通过
- **日志**：控制台 + `output/logs/noteforge.log` 双写
- **飞书分类**：`feishu_client.match_category()` 支持 `match` 列表格式（扁平匹配）

## 飞书知识库分类准则（稳定结构，勿随意修改）

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

**排序**：跨集提炼在上（高层知识），逐集笔记在下（原始素材），其他笔记始终最后

## 已知限制

- **飞书 wiki 节点不支持重命名**：lark-cli 的 PATCH 方法无法路由到飞书 wiki 节点更新接口（`PATCH /wiki/v2/spaces/{space_id}/nodes/{node_id}` 返回 404）。已有节点的标题只能在飞书网页端手动修改。新创建的节点可通过 `ensure_category_node` 自动加前缀。
- 小宇宙/荔枝FM 的 API 是未公开接口，可能随平台更新变化
- 喜马拉雅仅 yt-dlp 单策略，无 API 降级；`/album/` 链接不支持
- DRM 平台（Spotify/Apple Music/网易云/QQ 音乐）无法提取音频
- 微信视频号因封闭生态无法自动化支持
