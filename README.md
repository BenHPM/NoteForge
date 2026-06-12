# NoteForge

> **智能笔记锻造系统** — 将视频/播客/音频内容自动转写并提炼为高质量学习笔记

[![Status](https://img.shields.io/badge/status-Ready-brightgreen)](https://github.com/BenHPM/NoteForge)
[![Engine](https://img.shields.io/badge/engine-Paraformer%20(FunASR)-blue)](https://github.com/modelscope/FunASR)
[![LLM](https://img.shields.io/badge/LLM-Claude%20%2F%20OpenAI%20%2F%20Local-yellow)](#)

---

## 核心特性

- 🎬 **视频转笔记**: Paraformer ASR (96%+ 准确率) + LLM 知识提炼，一键完成
- 🧠 **知识提炼**: 不只是整理原文，提取可迁移的框架、思维模型和行动方法论
- 🎙️ **播客 RSS**: 订阅 podcast，自动下载、转写、生成笔记
- 📺 **YouTube**: 输入 URL，自动下载音频+转写+笔记
- 🛡️ **9 维质量门禁**: R1-R9 自动评分 + 反馈重试循环
- 📊 **知识合成**: 跨集知识框架、思维模型图谱、行动手册自动生成
- 🔍 **笔记搜索**: 关键词搜索 + 标签提取 + 历史笔记关联

---

## 快速开始

### 方法一：双击运行（推荐）
```
双击: video-to-text\noteforge.bat
```

### 方法二：命令行
```powershell
$py = "D:\ProgramData\TraeCN\NoteForge\video-to-text\envs\paraformer\python.exe"

# 从视频生成笔记（完整流程）
& $py "video-to-text\scripts\llm_note_engine.py" --input video.mp4

# 从转写文本生成笔记
& $py "video-to-text\scripts\llm_note_engine.py" --input ep01

# YouTube 视频
& $py "video-to-text\scripts\llm_note_engine.py" --youtube "https://youtube.com/watch?v=..."

# 播客订阅
& $py "video-to-text\scripts\llm_note_engine.py" --podcast-subscribe "https://feeds.xxx/podcast.xml"

# 知识合成（跨集提炼）
& $py "video-to-text\scripts\llm_note_engine.py" --mode synthesis

# 批量生成
& $py "video-to-text\scripts\llm_note_engine.py" --batch --skip-existing
```

---

## 项目结构

```
video-to-text/
├── config/
│   ├── llm_engine_config.yaml      # LLM 提供商/质量/路径配置
│   ├── note_generation_rules.yaml  # R1-R9 质量规则定义
│   ├── experience_log.yaml         # 历史错误模式（注入 prompt）
│   ├── video-mapping.json          # 视频/episode 元数据映射
│   └── podcast_feeds.json          # 播客订阅源配置
├── scripts/
│   ├── llm_note_engine.py          # 主引擎（编排全流程）
│   ├── llm_providers.py            # LLM 提供商抽象（Claude/OpenAI/Local）
│   ├── prompt_builder.py           # Prompt 组装（规则+经验+格式）
│   ├── note_formatter.py           # 笔记后处理 + 结构校验
│   ├── transcript_preprocessor.py  # 转写文本清洗 + 分块
│   ├── quality_gate.py             # 9 维质量评分引擎
│   ├── paraformer_transcribe.py    # Paraformer ASR 转写
│   ├── youtube_handler.py          # YouTube 音频下载
│   ├── podcast_handler.py          # 播客 RSS 订阅管理
│   └── knowledge_index.py          # 笔记搜索 + 标签 + 关联
├── envs/paraformer/                # Python 3.10 独立环境
├── noteforge.bat                   # 统一 CLI 入口（12 选项）
└── output/
    ├── notes/                      # 生成的笔记
    ├── transcripts/                # 转写文本
    ├── quality_reports/            # 质量评估报告
    └── audio/                      # 下载的音频（YouTube/播客）
```

---

## 质量控制系统

9 维评分引擎，确保笔记准确且有深度：

| 规则 | 名称 | 严重度 | 说明 |
|------|------|--------|------|
| R1 | 禁止虚构数据 | fatal | 数字必须有原文出处 |
| R2 | 禁止越界增补 | fatal | 不得添加原文不存在的内容 |
| R3 | 禁止事实反转 | fatal | 不得翻转原文语义方向 |
| R4 | 禁止概念失真 | major | 保留专业术语的关键限定词 |
| R5 | 覆盖度底线 | major | 核心内容覆盖率 ≥ 80% |
| R6 | 术语一致性 | medium | 术语表与正文使用一致 |
| R7 | 框架完整性 | major | 框架不得丢失关键组成要素 |
| R8 | 洞察可行动性 | major | 洞察必须有具体行动指引 |
| R9 | 分层准确性 | medium | 区分个案经验和通用原则 |

### 质量门禁流程
```
LLM 生成笔记 → 9 维评分 → 通过？→ 输出
                              ↓ 未通过
                    反馈 prompt（含行号+问题+建议）
                              ↓
                    LLM 修正 → 重新评分 → max 2 次重试
```

---

## CLI 参考

### 笔记生成
```bash
python llm_note_engine.py --input ep01               # 从集数编号生成
python llm_note_engine.py --input file.mp3           # 从音频文件生成
python llm_note_engine.py --input ep01 --force       # 覆盖已有笔记
python llm_note_engine.py --batch --skip-existing    # 批量生成
python llm_note_engine.py --mode synthesis           # 知识合成
python llm_note_engine.py --check-only note.md       # 仅质量检查
```

### YouTube
```bash
python llm_note_engine.py --youtube "URL"            # 单视频
python llm_note_engine.py --youtube-playlist "URL"   # 播放列表
```

### 播客 RSS
```bash
python llm_note_engine.py --podcast-subscribe "RSS_URL"  # 订阅
python llm_note_engine.py --podcast-list                 # 列出订阅
python llm_note_engine.py --podcast-sync NAME            # 查看新 episodes
python llm_note_engine.py --podcast-process NAME         # 下载+转写+笔记
python llm_note_engine.py --podcast-unsubscribe NAME     # 取消订阅
```

### 搜索
```bash
python llm_note_engine.py --search "关键词"           # 搜索笔记
python llm_note_engine.py --list-notes                # 列出所有笔记
python llm_note_engine.py --tags "标签"               # 按标签过滤
```

---

## 依赖

### Python 环境
- Python 3.10（`envs/paraformer/` 独立环境）

### 已安装包
- `requests` — HTTP 请求
- `PyYAML` — 配置文件解析
- `tiktoken` — Token 估算
- `jieba` — 中文分词（搜索功能）
- `funasr` + `torch` — Paraformer ASR 转写

### 外部工具
- `ffmpeg` — 音频提取（需在 PATH 中）
- `yt-dlp` — YouTube 下载（可选，`pip install yt-dlp`）

### LLM API
- 配置文件: `config/llm_engine_config.yaml`
- 支持: Claude API / OpenAI API / 本地模型（Ollama 等）
- API Key: 通过环境变量或配置文件直接指定

---

## 性能参考

| 视频时长 | 转写耗时 | 笔记生成耗时 | 总耗时 |
|---------|---------|------------|--------|
| 10 分钟 | ~2 分钟 | ~80 秒 | ~4 分钟 |
| 70 分钟 | ~15 分钟 | ~90 秒 | ~18 分钟 |
| 96 分钟 | ~27 分钟 | ~90 秒 | ~30 分钟 |

---

## 许可证

本项目仅供个人学习研究使用。

---

**最后更新**: 2026-06-12
**版本**: v2.0 (NoteForge + LLM Engine)
