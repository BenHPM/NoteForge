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
- 📺 **YouTube**: 输入 URL，自动下载音频+转写+笔记
- 🎙️ **播客 RSS**: 订阅 podcast，自动下载、转写、生成笔记
- 🛡️ **9 维质量门禁**: R1-R9 自动评分 + 反馈重试循环
- 📊 **知识合成**: 跨集知识框架、思维模型图谱、行动手册自动生成
- 🔍 **笔记搜索**: 关键词搜索 + 标签提取 + 历史笔记关联
- 📚 **飞书知识库同步**: 笔记一键同步到飞书 Wiki，支持增量更新和分类组织

---

## 快速开始

### 方法一：双击运行（推荐）
```
双击: video-to-text\noteforge.bat
```

### 方法二：命令行
```powershell
# 在项目根目录下运行（路径根据你的实际安装位置调整）
$py = "video-to-text\envs\paraformer\python.exe"

# 从视频生成笔记（完整流程）
& $py "video-to-text\scripts\llm_note_engine.py" --input video.mp4

# 从转写文本生成笔记
& $py "video-to-text\scripts\llm_note_engine.py" --input ep01

# YouTube 视频
& $py "video-to-text\scripts\llm_note_engine.py" --youtube "https://youtube.com/watch?v=..."

# 批量生成
& $py "video-to-text\scripts\llm_note_engine.py" --batch --skip-existing

# 批量质量评分
& $py "video-to-text\scripts\batch_quality.py"

# 飞书同步
python scripts/feishu_sync.py
```

---

## 项目结构

```
NoteForge/
├── scripts/
│   └── feishu_sync.py              # 飞书同步入口脚本
├── video-to-text/
│   ├── config/
│   │   ├── llm_engine_config.yaml  # LLM 提供商/质量/路径/飞书配置
│   │   ├── note_generation_rules.yaml  # R1-R9 质量规则定义
│   │   ├── experience_log.yaml     # 历史错误模式（注入 prompt）
│   │   ├── video-mapping.json      # 视频/episode 元数据映射
│   │   └── podcast_feeds.json      # 播客订阅源配置
│   ├── scripts/
│   │   ├── llm_note_engine.py      # 主引擎（编排全流程）
│   │   ├── llm_providers.py        # LLM 提供商抽象（Claude/OpenAI/Local）
│   │   ├── prompt_builder.py       # Prompt 组装（规则+经验+格式）
│   │   ├── note_formatter.py       # 笔记后处理 + 结构校验
│   │   ├── transcript_preprocessor.py  # 转写文本清洗 + 分块
│   │   ├── quality_gate.py         # 9 维质量评分引擎
│   │   ├── batch_quality.py        # 批量质量评分脚本
│   │   ├── feishu_client.py        # 飞书知识库 API 客户端
│   │   ├── paraformer_transcribe.py    # Paraformer ASR 转写
│   │   ├── youtube_handler.py      # YouTube 音频下载
│   │   ├── podcast_handler.py      # 播客 RSS 订阅管理
│   │   └── knowledge_index.py      # 笔记搜索 + 标签 + 关联
│   ├── envs/paraformer/            # Python 3.10 独立环境
│   ├── noteforge.bat               # 统一 CLI 入口
│   └── output/
│       ├── notes/                  # 生成的笔记（.gitignore）
│       ├── transcripts/            # 转写文本（.gitignore）
│       ├── quality_reports/        # 质量评估报告（.gitignore）
│       └── audio/                  # 下载的音频（.gitignore）
├── .env                            # 飞书 APP_ID/SECRET（.gitignore）
└── README.md
```

---

## 飞书知识库同步

NoteForge 支持将生成的笔记一键同步到飞书知识库（Wiki），实现知识的云端组织和团队共享。

### 架构设计

```
本地笔记 (.md)
    │
    ▼
feishu_sync.py ─── 扫描 + 分组（按 categories 配置）
    │
    ▼
feishu_client.py ─── lark-cli 子进程调用飞书 API
    │
    ├─ wiki/v2/spaces/{space_id}/nodes    → 创建/查找知识库节点
    ├─ docx/v1/documents/{id}/blocks      → 写入文档内容
    └─ Markdown → 飞书 Block 转换          → 标题/列表/粗体/代码块
    │
    ▼
飞书知识库（Wiki）
    ├── 分类A/
    │   ├── 知识体系
    │   ├── 第01集-xxx
    │   └── ...
    └── 分类B/
        └── xxx
```

### 实现方案

#### 1. API 调用层 — `feishu_client.py`

通过 `lark-cli` 命令行工具调用飞书 API（用户身份认证），封装了：

- **节点操作**: `list_child_nodes()`, `find_node_by_title()`, `create_node()`, `ensure_category_node()`
- **文档操作**: `get_document_blocks()`, `delete_block_children()`, `overwrite_document()`, `append_blocks()`
- **批量写入**: 分批追加 blocks（默认 50 个/批），避免单次请求过大
- **幂等创建**: `create_node()` 自动检查节点是否已存在，避免重复创建

```python
# 核心调用方式 — 通过 lark-cli 子进程
cmd = [lark_cli_path, "api", "--as", "user", method, path]
# lark-cli 自动管理 token 刷新，无需手动处理 OAuth
```

#### 2. Markdown → 飞书 Block 转换

`md_to_blocks()` 函数将 Markdown 解析为飞书文档 block 列表：

| Markdown 语法 | 飞书 Block 类型 | 说明 |
|--------------|----------------|------|
| `# ~ ######` | heading1-6 | 标题层级 |
| 普通文本 | text (type=2) | 正文段落 |
| `**粗体**` | text_element_style.bold | 行内粗体 |
| `` `代码` `` | text_element_style.inline_code | 行内代码 |
| `~~删除线~~` | text_element_style.strikethrough | 删除线 |
| `- 列表` | text (带 `•` 前缀) | 无序列表（飞书 list block 有兼容问题） |
| `1. 列表` | text (保留序号) | 有序列表 |
| `> 引用` | text (带 `>` 前缀) | 引用块 |
| `` ``` `` | code (type=23) | 代码块 |
| `---` | 跳过 | 分隔线 |

> **注意**: 飞书 API 的 list block 和 quote_container 存在兼容问题，因此统一转为带前缀的普通文本块，确保内容完整性。

#### 3. 分类组织 — `categories` 配置

在 `llm_engine_config.yaml` 的 `feishu.categories` 中定义知识库的目录结构：

```yaml
feishu:
  categories:
    - name: "课程笔记"                # 父节点
      children:
        - pattern: "*知识体系*"       # 子节点匹配规则
          node_title: "知识体系"
          order: 0
        - pattern: "第*集*"           # 匹配所有课程笔记
          node_title: null            # 使用原始文件名
          order: 1
    - name: "其他笔记"                # 兜底分类
      pattern: "*"                    # 匹配所有未分类文件
```

匹配逻辑：
1. 按 `categories` 配置顺序依次匹配（先匹配优先）
2. `children` 内的子分类先匹配，未匹配的文件落入父分类的 `pattern`
3. `exclude_patterns` 排除不需要同步的文件（如测试文件）

#### 4. 同步策略

| 模式 | 命令 | 说明 |
|------|------|------|
| 全量同步 | `python feishu_sync.py` | 扫描所有笔记，已存在的更新内容，新增的创建节点 |
| 仅新增 | `python feishu_sync.py --new-only` | 跳过已存在的节点，只同步新增笔记 |
| 预览模式 | `python feishu_sync.py --dry-run` | 打印同步计划，不执行 API 调用 |
| 单文件 | `python feishu_sync.py --file "关键词"` | 只同步匹配关键词的文件 |
| 清理重建 | `python feishu_sync.py --clean --clean-confirm` | 删除飞书所有内容后重新同步 |

同步流程：
```
扫描 output/notes/*.md
    ↓
按 categories 配置分组
    ↓
遍历每组：
  ├─ ensure_category_node() → 确保父节点存在
  ├─ find_node_by_title()  → 检查文档是否已存在
  ├─ 已存在 → overwrite_document() → 清空旧内容 + 写入新 blocks
  └─ 不存在 → create_document_and_write() → 创建节点 + 写入 blocks
    ↓
输出同步汇总（同步/跳过/错误 数量）
```

### 配置说明

在 `config/llm_engine_config.yaml` 中配置飞书同步参数：

```yaml
feishu:
  enabled: true                     # 总开关
  auto_sync: false                  # 笔记生成后自动同步（预留）
  space_id: "your_space_id"         # 飞书知识库 space_id
  root_node_token: "your_token"     # 根节点 token

  exclude_patterns:                 # 排除不同步的文件
    - "*test*"
    - "*测试*"

  categories:                       # 分类结构（见上文）
    - name: "分类名"
      pattern: "*"

  api_interval: 0.5                 # API 请求间隔（秒）
  block_batch_size: 50              # 每批写入 block 上限
```

### 前置条件

1. **安装 lark-cli**: `npm install -g @anthropic-ai/lark-cli`（或参考 [lark-cli 文档](https://github.com/anthropics/lark-cli)）
2. **认证**: `lark-cli auth login` — 完成飞书 OAuth 授权
3. **应用权限**: 飞书应用需要 `wiki:wiki` + `docx:document` 权限
4. **环境变量**: 在项目根目录 `.env` 文件中配置 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`

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

### 批量质量评分

```bash
# 评分所有笔记
python batch_quality.py

# 跳过已有报告的笔记
python batch_quality.py --skip-existing

# 只预览映射关系，不实际评分
python batch_quality.py --dry-run
```

输出示例：
```
批量质量评分汇总
============================================================
总计: 21 个笔记
已评分: 18
通过: 18
未通过: 0
平均分: 0.92
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

### 飞书同步
```bash
python feishu_sync.py                                # 全量同步
python feishu_sync.py --dry-run                      # 预览模式
python feishu_sync.py --new-only                     # 仅同步新增
python feishu_sync.py --file "关键词"                 # 同步单文件
python feishu_sync.py --category "分类名"              # 同步单分类
python feishu_sync.py --clean --clean-confirm        # 清理重建
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
- `lark-cli` — 飞书 API 调用（可选，飞书同步功能需要）

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

**最后更新**: 2026-06-17
**版本**: v2.1 (NoteForge + LLM Engine + Feishu Sync)
