# NoteForge 架构重构规划

> 版本：v1.0 | 日期：2026-06-27 | 状态：规划中，暂不实施
>
> 本文档为后续迭代提供路线图，不与当前功能开发耦合。

---

## 一、当前状态

| 指标 | 数值 |
|------|------|
| Python 源文件 | 16 + 2 顶层脚本 |
| 总代码行数 | ~9,300 行 |
| 测试 | 1 文件 / 64 用例 / 14 类 |
| 最大单文件 | `llm_note_engine.py` 2,389 行 |
| print() 调用 | 250+ 处 |
| 静默吞错 | 21 处 `except Exception:` 无日志 |
| 架构级技术债 | 6 项（见下） |

---

## 二、技术债清单（按影响排序）

### T1. God Object — `llm_note_engine.py` 2389 行

**问题**：`LLMNoteEngine` 类承载 8 项独立职责，`main()` 函数 748 行混合 6+ 下载策略与 CLI 解析。

**影响**：
- 难以定位 bug（修改笔记生成可能意外影响合成）
- 无法独立测试子模块
- 代码审查困难

**拆分方案**（9 个模块）：

| 新模块 | 行数 | 关键类 | 职责 |
|--------|------|--------|------|
| `models.py` | ~15 | `GenerationResult` | 共享数据结构 |
| `domain_classifier.py` | ~130 | `DomainClassifier` | 知识域分类 + 缓存 |
| `quality_manager.py` | ~100 | `QualityManager` | 质量门禁评估 + 报告 |
| `audio_handler.py` | ~145 | `AudioHandler` | ASR 转写 + 标题提取 + 转写定位 |
| `external_sync.py` | ~55 | `FeishuSync` | 飞书同步（可选） |
| `synthesis_engine.py` | ~510 | `SynthesisEngine` | 三种合成变体 |
| `batch_processor.py` | ~130 | `BatchProcessor` | 批量编排 + 摘要 |
| `cli.py` | ~750 | `MediaDownloader` + `main()` | CLI 解析 + URL 处理 + 调度 |
| `llm_note_engine.py` | ~350 | `LLMNoteEngine` | 核心单笔记生成，组合以上模块 |

**依赖关系**：
```
cli.py → LLMNoteEngine
       → MediaDownloader (YouTube/B站/音频平台)
LLMNoteEngine → DomainClassifier
              → QualityManager
              → AudioHandler
              → SynthesisEngine
              → BatchProcessor
              → FeishuSync
SynthesisEngine → DomainClassifier
BatchProcessor → LLMNoteEngine.generate_note
```

**实施优先级**：🔴 高 — 其他改进（测试、日志统一）都依赖于此拆分

---

### T2. print/logging 混用 — 250+ print 调用

**问题**：3 种输出模式混杂：
- 正确使用 `logging`（`llm_providers.py`、`podcast_handler.py`、`feishu_client.py`）
- 仅用 `print()` 无 `logging`（`paraformer_transcribe.py` 48 处、`feishu_sync.py` 45 处、`bilibili_download.py` 14 处）
- `logging` + `print` 混用（`llm_note_engine.py` 38 print + 30 logging）

**按文件统计**：

| 文件 | print | logging | 优先级 |
|------|-------|---------|--------|
| `paraformer_transcribe.py` | 48 | 0 | 🔴 |
| `feishu_sync.py` | ~45 | 0 | 🔴 |
| `bilibili_download.py` | 14 | 0 | 🟡 |
| `llm_note_engine.py` | 38 | 30+ | 🟢（main() 中可接受） |
| `quality_gate.py` | 6 | 2 | 🟢 |
| 其他 | 0 | 正常 | ✅ |

**改造原则**：
- `main()` 中的 CLI 用户交互 `print()` → 保留
- 状态/进度 `print(f"[INFO/OK]...")` → `logger.info()`
- 错误 `print(f"[ERROR]...")` → `logger.error()`
- 调试 `print(f"[DEBUG]...")` → `logger.debug()`

**配套改进**：
- 创建 `scripts/logging_config.py` 统一 `basicConfig()` 和文件 handler（当前 3 处独立配置）
- 提取 ANSI 颜色常量到 `scripts/ansi_colors.py`（当前 `env_check.py` 和 `feishu_sync.py` 硬编码 `\033[`）

**实施优先级**：🟡 中 — 与 T1 拆分可并行，但建议拆分后在新模块中直接用 logging

---

### T3. 静默吞错 — 21 处 `except Exception:` 无日志

**分类**：

| 类别 | 数量 | 处理方式 |
|------|------|---------|
| 清理（temp 文件删除） | 4 | 可接受，但统一加 `logger.debug` |
| 循环 continue（跳过失败项） | 5 | 加 `logger.debug` |
| 静默回退（返回默认值） | 12 | 加 `logger.warning` 或 `logger.debug` |

**高频问题文件**：
- `knowledge_index.py`：3 处（read_text 失败 → 0.0 / "" / continue）
- `topic_classifier.py`：3 处（同上模式）
- `llm_note_engine.py`：5 处（域检测/关联笔记/标题/转写映射）

**额外发现**：
- `podcast_handler.py:101` — `except (PodcastError, Exception): pass` 等价于 `except Exception: pass`（PodcastError 是 Exception 子类）
- `llm_note_engine.py:2514` — 错误消息无上下文前缀
- `paraformer_transcribe.py` — 转写失败时 238 行无 traceback vs 293 行有 traceback（不一致）

**统一方案**：创建 `_safe_read_text(path) -> Optional[str]` 工具函数，统一文件读取的异常处理和日志

**实施优先级**：🟡 中 — 逐文件推进，与 T2 日志改造同步

---

### T4. 测试覆盖不足

**当前覆盖**（1 文件 / 64 用例）：

| 模块 | 测试状态 | 关键未测路径 |
|------|---------|-------------|
| `llm_note_engine.py` | 仅 `detect_domain` 2 用例 | generate_note, synthesis, batch |
| `llm_providers.py` | ❌ 无测试 | provider 创建、代理降级、重试 |
| `token_manager.py` | ❌ 无测试 | 成本估算、日志持久化 |
| `podcast_handler.py` | ❌ 无测试 | RSS 解析、订阅管理 |
| `youtube_handler.py` | ❌ 无测试 | yt-dlp 集成 |
| `knowledge_index.py` | ❌ 无测试 | 搜索、标签 |
| `bilibili_download.py` | 5 用例 | 下载策略降级 |
| `paraformer_transcribe.py` | ❌ 无测试 | ASR 转写（需 mock） |
| `auto_pipeline.py` | ❌ 无测试 | 断点续传、自动合成 |
| `compare_notes.py` | ❌ 无测试 | 版本对比 |

**测试优先级**：

| 优先级 | 模块 | 理由 |
|--------|------|------|
| 🔴 P0 | `llm_providers.py` | 核心依赖，代理降级/retry 逻辑关键 |
| 🔴 P0 | `prompt_builder.py` | Prompt 组装是生成质量的关键 |
| 🟡 P1 | `token_manager.py` | 成本追踪，计算逻辑独立可测 |
| 🟡 P1 | `knowledge_index.py` | 搜索/标签纯计算逻辑 |
| 🟢 P2 | `bilibili_download.py` | 扩展现有 5 用例 |
| 🟢 P2 | `podcast_handler.py` | RSS 解析可 mock |

**实施策略**：
1. T1 拆分后，每个新模块自带测试文件
2. 优先为纯计算逻辑编写单元测试
3. I/O 重模块（ASR、下载）用 mock/subprocess 注入

**实施优先级**：🟡 中 — 依赖 T1 拆分，拆分后逐步补充

---

### T5. 依赖管理

**问题 1**：`requirements.txt` 中 `torch==2.2.0+cpu` 和 `torchaudio==2.2.0+cpu` 带 `+cpu` 标记，直接 `pip install -r requirements.txt` 会失败，需要：
```bash
pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
```

**问题 2**：4 个传递依赖不应显式声明（代码未直接 import）：
- `scipy==1.15.3` — librosa/torch 传递依赖
- `librosa==0.11.0` — FunASR 内部依赖
- `onnxruntime==1.23.2` — FunASR 内部依赖
- `numpy==1.26.4` — torch/scipy 传递依赖

**问题 3**：`yt-dlp` 通过 subprocess 调用但未列入 requirements.txt

**方案**：
- 将 ASR 相关依赖拆到 `requirements-asr.txt`
- 主 `requirements.txt` 只保留直接依赖
- 添加注释说明 `--extra-index-url`
- `yt-dlp` 添加到 requirements.txt 或以注释说明

**实施优先级**：🟢 低 — 不影响功能，仅影响新环境部署

---

### T6. 配置加载碎片化

**问题**：多个文件独立加载 `llm_engine_config.yaml`，各自 `yaml.safe_load()`，无共享配置对象：
- `llm_note_engine.py` — 主配置
- `quality_gate.py` — 阈值配置
- `auto_pipeline.py` — 代理 URL + 域映射
- `prompt_builder.py` — 路径配置
- `token_manager.py` — 日志路径

**方案**：创建 `config_loader.py` 单例，一次加载、全局共享：
```python
class NoteForgeConfig:
    _instance = None
    def __init__(self, config_path):
        self._data = yaml.safe_load(...)
    @classmethod
    def load(cls, config_path=None):
        if cls._instance is None:
            cls._instance = cls(config_path or default_path)
        return cls._instance
```

**实施优先级**：🟢 低 — 与 T1 拆分一并实施

---

## 三、实施路线图

### 阶段 1：核心拆分（预计 2-3 个工作单元）

> **前置条件**：当前功能全部正常，64 测试通过

1. **提取 `models.py`** — `GenerationResult` 数据类，零风险
2. **提取 `domain_classifier.py`** — 自包含，有测试基础
3. **提取 `cli.py`** — `main()` + argparse + `MediaDownloader`，最大收益
4. **提取 `synthesis_engine.py`** — 最大学科块，需注意 `DomainClassifier` 注入
5. **提取 `quality_manager.py`** — 质量门禁封装
6. **提取 `audio_handler.py`** — ASR + 标题提取
7. **提取 `external_sync.py`** — 飞书同步
8. **提取 `batch_processor.py`** — 批量编排
9. **瘦化 `llm_note_engine.py`** — 仅保留核心生成 + 基础设施

**每步验证**：
- 64 现有测试全部通过
- `--input` / `--batch` / `--mode synthesis-2stage` / `--check-only` 端到端验证

### 阶段 2：日志统一（预计 1-2 个工作单元）

1. 创建 `logging_config.py` + `ansi_colors.py`
2. 改造 `paraformer_transcribe.py`（48 → ~18 print）
3. 改造 `feishu_sync.py`（45 → ~25 print）
4. 改造 `bilibili_download.py`（14 → ~3 print）
5. 修复 21 处静默吞错（加 `logger.debug`/`warning`）
6. 创建 `_safe_read_text()` 工具函数

### 阶段 3：测试补充（预计 2-3 个工作单元）

1. `llm_providers.py` 测试（代理降级、retry、Prompt Caching）
2. `prompt_builder.py` 测试（4 种 content_type）
3. `token_manager.py` 测试
4. `knowledge_index.py` 测试
5. 扩展 `bilibili_download.py` 测试
6. 新模块测试（阶段 1 产出）

### 阶段 4：收尾优化（预计 1 个工作单元）

1. 依赖管理重构（`requirements-asr.txt` 拆分）
2. 配置加载统一（`config_loader.py`）
3. `podcast_handler.py:101` 修复
4. 文档更新（CLAUDE.md 反映新结构）

---

## 四、风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 拆分引入循环导入 | 中 | 高 | 先画依赖图，models.py 作为叶子节点 |
| 拆分后 main() 行为变化 | 低 | 高 | 每步端到端验证 |
| 日志改造影响用户输出 | 中 | 低 | CLI 交互 print 保留不变 |
| ASR 环境测试困难 | 高 | 低 | mock subprocess，不依赖实际模型 |
| 配置加载顺序敏感 | 低 | 中 | 测试覆盖 lazy import 路径 |

---

## 五、验收标准

- [ ] `llm_note_engine.py` ≤ 400 行
- [ ] 所有模块 ≤ 600 行
- [ ] 0 处 `print()` 在非 `main()` 函数中（除 CLI 交互保留）
- [ ] 0 处 `except Exception:` 无日志
- [ ] 测试 ≥ 120 用例（当前 64 → 翻倍）
- [ ] `requirements.txt` 可直接 `pip install`（ASR 依赖单独拆分）
- [ ] 64 现有测试 100% 通过（回归保证）
