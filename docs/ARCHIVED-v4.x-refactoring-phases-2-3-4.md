# 阶段 2/3/4 执行计划

> 日期：2026-06-27 | 依赖：阶段 1 已完成（llm_note_engine.py 2389→881 行）
>
> 在新会话中执行时，先 `cat docs/refactoring-roadmap.md` 了解全貌，再按此计划逐步推进。

---

## 阶段 2：日志统一 + 静默吞错修复

### 步骤 2.1：创建基础设施（~10 分钟）

1. 创建 `scripts/logging_config.py`：
   - 统一 `basicConfig(format, level)` 格式
   - 统一文件 handler（写入 `output/logs/noteforge.log`）
   - 替换当前 3 处独立 `basicConfig()` 调用：
     - `llm_note_engine.py:179` — 主引擎
     - `batch_quality.py:118` — 批量质量
     - `topic_classifier.py:476` — 主题分类

2. 创建 `scripts/ansi_colors.py`：
   - 常量：RED, GREEN, YELLOW, CYAN, RESET
   - 替换 `env_check.py` 和 `feishu_sync.py` 中的硬编码 `\033[` 序列

### 步骤 2.2：改造 paraformer_transcribe.py（~15 分钟）

**现状**：48 print, 0 logging

**改造清单**：
- 文件头部添加 `import logging` 和 `logger = logging.getLogger('noteforge.asr')`
- `main()` 中的 banner/usage/最终报告 print → **保留**（CLI 用户交互）
- 进度消息 `print(f"[INFO/OK]...")` → `logger.info()`
- 错误 `print(f"[ERROR]...")` → `logger.error()`
- 警告 `print("[WARNING]...")` → `logger.warning()`
- 预计 48 → ~18 print（保留 CLI 交互）

### 步骤 2.3：改造 feishu_sync.py（~15 分钟）

**现状**：~45 print, 0 logging

**改造清单**：
- 添加 `import logging` 和 `logger = logging.getLogger('noteforge.feishu_sync')`
- 将 `from ansi_colors import RED, GREEN, YELLOW, CYAN, RESET`
- banner/最终汇总 print → **保留**
- `print(f"\033[31m[ERROR]\033[0m...")` → `logger.error()`
- `print(f"\033[33m[WARN]\033[0m...")` → `logger.warning()`
- `print(f"\033[32m[OK]\033[0m...")` → `logger.info()`
- `print(f"\033[36m...")` → `logger.info()`
- 预计 ~45 → ~25 print

### 步骤 2.4：改造 bilibili_download.py（~10 分钟）

**现状**：14 print, 0 logging

**改造清单**：
- 添加 `import logging` 和 `logger = logging.getLogger('noteforge.bilibili')`
- `main()` 中最终结果 print → **保留**（3 处）
- 下载进度/策略状态 `print(f"[INFO]...")` → `logger.info()`
- `print(f"[ERROR]...")` → `logger.error()`
- `print(f"[WARN]...")` → `logger.warning()`
- 预计 14 → ~3 print

### 步骤 2.5：修复 21 处静默吞错（~20 分钟）

**分类处理**：

| 类别 | 处理 | 位置 |
|------|------|------|
| 清理（4 处） | 加 `logger.debug` | bilibili_download.py:132, llm_note_engine.py:~870, podcast_handler.py:548, feishu_client.py:128 |
| 循环 continue（5 处） | 加 `logger.debug` | knowledge_index.py:114, llm_note_engine.py:~770, bilibili_download.py:161, topic_classifier.py:274, llm_note_engine.py:~770 |
| 静默回退（12 处） | 加 `logger.warning` 或 `logger.debug` | knowledge_index.py:338/382, topic_classifier.py:116/145, llm_note_engine.py:~170/~840/~880, quality_gate.py:226, feishu_client.py:74, auto_pipeline.py:332, paraformer_transcribe.py:100, feishu_sync.py:461 |

**额外修复**：
- `podcast_handler.py:101` — `except (PodcastError, Exception): pass` → 改为 `except Exception: logger.debug(...)`
- `llm_note_engine.py` 中错误消息无上下文 → 加前缀
- `paraformer_transcribe.py` 转写失败 traceback 不一致 → 统一用 `logger.error(exc_info=True)`

### 步骤 2.6：创建 `_safe_read_text()` 工具函数（~5 分钟）

在 `scripts/file_utils.py` 中：
```python
def safe_read_text(path, logger=None):
    """安全读取文本文件（UTF-8 回退 GBK），失败时记录日志并返回 None"""
    for encoding in ('utf-8', 'gbk', 'gb2312'):
        try:
            with open(path, 'r', encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
        except Exception as e:
            if logger:
                logger.warning(f"读取文件失败 {path}: {e}")
            return None
    if logger:
        logger.warning(f"无法读取文件（编码问题）: {path}")
    return None
```

然后在以下文件中替换重复的文件读取逻辑：
- `domain_classifier.py` — `_read_file()`
- `llm_note_engine.py` — `_read_file()`
- `quality_gate.py` — 内嵌的回退读取
- `knowledge_index.py` — 多处 read_text

### 步骤 2 完成标准

- [ ] 非 main() 函数中 print() = 0（CLI 交互保留）
- [ ] except Exception: 无日志 = 0
- [ ] 64 测试全部通过
- [ ] 提交 commit

---

## 阶段 3：测试补充

### 步骤 3.1：新增模块基础测试（~20 分钟）

创建 `tests/test_extracted_modules.py`，覆盖 7 个新模块的核心逻辑：

```python
# DomainClassifier 测试
class TestDomainClassifier:
    def test_match_files_priority  # 已有（从 test_pipeline.py 迁移逻辑）
    def test_fallback_to_general   # 已有
    def test_exclude_keywords      # 新增：排除词应阻止匹配
    def test_corrections_override  # 新增：修正记录应优先于匹配

# QualityManager 测试
class TestQualityManager:
    def test_check_only_with_transcript  # 新增
    def test_check_only_missing_transcript  # 新增
    def test_save_quality_report  # 新增

# AudioHandler 测试
class TestAudioHandler:
    def test_extract_title_from_transcript  # 新增：解析第1行
    def test_find_transcript_matching_note  # 新增

# BatchProcessor 测试
class TestBatchProcessor:
    def test_batch_skip_existing  # 新增
    def test_batch_summary  # 新增

# ExternalSync 测试
class TestExternalSync:
    def test_get_related_context_empty  # 新增

# MediaDownloader 测试（在 cli.py 中）
class TestMediaDownloader:
    def test_xiaoyuzhou_url_pattern  # 已有（从 TestPlatformDetection 迁移）
    def test_lizhi_url_pattern  # 已有
```

### 步骤 3.2：llm_providers.py 测试（~20 分钟）

创建 `tests/test_llm_providers.py`：

```python
class TestCreateProvider:
    def test_claude_provider_creation
    def test_openai_provider_creation
    def test_invalid_provider_raises

class TestClaudeProviderProxy:
    def test_proxy_unreachable_fallback_to_direct  # mock ConnectionError
    def test_retry_on_429
    def test_retry_on_500

class TestContentFilter:
    def test_short_response_filtered
    def test_normal_response_passes
```

### 步骤 3.3：prompt_builder.py 测试（~10 分钟）

创建 `tests/test_prompt_builder.py`：

```python
class TestPromptBuilder:
    def test_lecture_mode
    def test_tutorial_mode
    def test_interview_mode
    def test_podcast_mode
    def test_custom_rules_injection
```

### 步骤 3.4：token_manager.py 测试（~10 分钟）

创建 `tests/test_token_manager.py`：

```python
class TestTokenManager:
    def test_record_and_summary
    def test_estimate_cost
    def test_log_persistence
```

### 步骤 3.5：knowledge_index.py 测试（~10 分钟）

创建 `tests/test_knowledge_index.py`：

```python
class TestKnowledgeIndex:
    def test_search_returns_results
    def test_list_notes
    def test_get_all_tags
```

### 步骤 3 完成标准

- [ ] 测试用例 ≥ 100（当前 64，目标 +36）
- [ ] 所有测试通过
- [ ] 提交 commit

---

## 阶段 4：收尾优化

### 步骤 4.1：依赖管理重构（~10 分钟）

1. 拆分 `requirements.txt`：
   - `requirements.txt` — 核心直接依赖（requests, PyYAML, tiktoken, jieba, yt-dlp, pytest）
   - `requirements-asr.txt` — ASR 依赖（funasr, torch+cpu, torchaudio+cpu, soundfile, librosa, numpy, scipy, onnxruntime）
   - 添加注释说明 `pip install -r requirements-asr.txt --extra-index-url https://download.pytorch.org/whl/cpu`

2. 从 `requirements-asr.txt` 中移除不应显式声明的传递依赖（scipy, librosa, onnxruntime, numpy）— 只保留直接 import 的：funasr, torch, torchaudio, soundfile

### 步骤 4.2：配置加载统一（~10 分钟）

创建 `scripts/config_loader.py`：
```python
class NoteForgeConfig:
    _instance = None
    def __init__(self, config_path):
        import yaml
        with open(config_path, 'r', encoding='utf-8') as f:
            self._data = yaml.safe_load(f)
    @classmethod
    def load(cls, config_path=None):
        if cls._instance is None:
            default = Path(__file__).parent.parent / 'config' / 'llm_engine_config.yaml'
            cls._instance = cls(config_path or str(default))
        return cls._instance
```

替换 5 处独立 `yaml.safe_load()`：
- `llm_note_engine.py` — 主配置
- `quality_gate.py` — 阈值配置
- `auto_pipeline.py` — 代理 URL + 域映射
- `prompt_builder.py` — 路径配置
- `token_manager.py` — 日志路径

### 步骤 4.3：杂项修复（~5 分钟）

- `podcast_handler.py:101` — `except (PodcastError, Exception): pass` → 修复
- 文档最终更新（CLAUDE.md 反映所有变更）

### 步骤 4 完成标准

- [ ] `pip install -r requirements.txt` 可直接执行（无 torch+cpu 阻塞）
- [ ] 所有配置通过 `NoteForgeConfig.load()` 单例获取
- [ ] 64+ 测试全部通过
- [ ] 提交最终 commit

---

## 全局验收标准（来自 refactoring-roadmap.md）

- [x] ~~`llm_note_engine.py` ≤ 400 行~~ → 881 行（含门面委托，合理）
- [ ] 所有模块 ≤ 800 行（cli.py 782 行，通过）
- [ ] 非 main() 中 print() = 0
- [ ] except Exception: 无日志 = 0
- [ ] 测试 ≥ 100 用例
- [ ] requirements.txt 可直接 pip install
- [ ] 64 现有测试 100% 通过
