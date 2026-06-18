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

- **ASR 环境**：必须用 `video-to-text/envs/paraformer/python.exe`（Python 3.10 + FunASR + torch）
- **LLM 代理**：配置在 `llm_engine_config.yaml` 的 `base_url`，默认 `http://127.0.0.1:15721`
  - 代理不可达时自动降级到直连 `https://api.anthropic.com`
  - 需要 `ANTHROPIC_API_KEY` 环境变量（或配置 `api_key`）
- **yt-dlp**：下载 YouTube/B站/音频平台音频，需在 PATH 中
- **ffmpeg**：视频提取音频，需在 PATH 中

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
- **飞书分类**：`feishu_client.match_category()` 支持嵌套 children 递归匹配

## 已知限制

- 小宇宙/荔枝FM 的 API 是未公开接口，可能随平台更新变化
- 喜马拉雅仅 yt-dlp 单策略，无 API 降级；`/album/` 链接不支持
- DRM 平台（Spotify/Apple Music/网易云/QQ 音乐）无法提取音频
- 微信视频号因封闭生态无法自动化支持
