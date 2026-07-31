# NoteForge CLI 执行可靠性 — 完整修复计划

> 来源：ForgeCouncil 多专家评审（SystemArchitect, DevOpsEngineer, SoftwareDeveloper, TheOpponent）
> 日期：2026-07-31
> 基线版本：v5.1.0

---

## 一、问题全景（18 项缺口 + 7 项未解决风险）

### 1.1 关键缺口（会导致全流程失败）

| # | 缺口 | 影响 | 涉及文件 | Council 优先级 |
|---|------|------|---------|---------------|
| 1 | **ASR 环境无 CLI 初始化命令** | 新机器/新用户无法一键创建 `envs/paraformer/` 环境 | `noteforge.bat:16-25`, `infra/env.py` | P0 |
| 2 | **飞书凭证硬编码在 YAML 中** | `space_id`/`root_node_token` 写死在配置，无验证 | `config/llm_engine_config.yaml:233-234`, `integration/feishu_sync.py:222-238` | P0 |
| 3 | **飞书认证无自动刷新/回退** | `lark-cli` token 过期后同步静默失败 | `integration/feishu.py:110-187`, `feishu_sync.py` | 延期 v5.1 |
| 4 | **Pipeline 无断点续传（主 CLI）** | `auto_pipeline.py` 有 resume，但 `main.py` 的 `run_batch()` 没有 | `engine/pipeline.py`, `batch/auto_pipeline.py`, `cli/main.py` | P0 |
| 5 | **缺少全局 dry-run 模式** | 只有 `feishu_sync` 有 `--dry-run` | `cli/main.py`, `cli/commands/*.py` | P1 |

### 1.2 高优先级缺口（造成大量人工干预）

| # | 缺口 | 影响 | 涉及文件 | Council 优先级 |
|---|------|------|---------|---------------|
| 6 | **质量报告无 CLI 查看器** | JSON 报告只能手动翻文件 | `quality/manager.py:160-175`, `cli/commands/check.py` | P1 |
| 7 | **质量阈值只能改 YAML** | 无法通过 CLI 临时覆盖 `min_score` | `config/llm_engine_config.yaml:70`, `cli/main.py` | P1 |
| 8 | **批量处理无进度持久化** | `BatchProcessor` 崩溃后从头开始 | `batch/processor.py`, `engine/note_engine.py:567-632` | P0 |
| 9 | **知识域检测/增量更新无 CLI** | 用户无法手动触发域检测 | `cli/commands/synthesis.py`, `core/domain_classifier.py` | P2 |
| 10 | **LLM 代理无健康检查** | `base_url` 不可达时到第一次调用才发现 | `core/llm_providers.py:250-278` | P0 |

### 1.3 中等优先级（不一致和调试痛苦）

| # | 缺口 | 涉及文件 | Council 优先级 |
|---|------|---------|---------------|
| 11 | ASR 依赖无 CLI 检查命令 | `sources/asr.py:158-228` | P1 |
| 12 | 无配置验证命令 | `config.py`, `config/llm_engine_config.yaml` | P1 |
| 13 | 飞书无认证引导 CLI | `integration/feishu_sync.py`, `integration/feishu.py` | P1 |
| 14 | 无 checkpoint 管理命令 | `batch/auto_pipeline.py:55-63` | P2 |
| 15 | 质量检查输出格式单一 | `cli/commands/check.py:6-15` | P2 |

### 1.4 低优先级（便利功能）

| # | 缺口 | Council 优先级 |
|---|------|---------------|
| 16 | 无知识域管理 CLI | P2 |
| 17 | 无 Provider 状态查看 | P2 |
| 18 | 无清理临时文件命令 | P2 |

### 1.5 Council 识别但未解决的风险

| # | 风险 | 严重性 | 说明 |
|---|------|--------|------|
| R1 | 质量门禁假阴性 | 高 | 3 次重试后 LLM 输出结构有效但语义垃圾，通过启发式但人眼可识别错误 |
| R2 | JSON 文件并发访问 | 中 | `video-mapping.json` / `podcast_feeds.json` 无文件锁或原子更新 |
| R3 | Prompt Caching 节省未验证 | 低 | 12% 声明无计量确认 |
| R4 | 代理降级延迟预算缺失 | 中 | 代理 10s+ 响应可能永不触发回退 |
| R5 | `scripts/_compat.py` 无退役计划 | 低 | 兼容性层可能无限积累 |
| R6 | Windows 专属路径假设 | 中 | 无 Linux/macOS CI 验证 |
| R7 | 质量报告无自动路由 | 低 | "质量门禁失败"是日志行，不是工单 |

---

## 二、修复计划（按阶段排序）

### 阶段 0：基线建立（立即，1-2 小时）

| # | 任务 | 文件 | 验收标准 |
|---|------|------|---------|
| 0.1 | 测量当前测试覆盖率 | 新增脚本 | `pytest --cov=noteforge --cov-report=term-missing` 输出基线百分比 |
| 0.2 | 归档过时文档 | `docs/refactoring-phases-2-3-4.md`, `docs/refactoring-roadmap.md` | 标记为 `ARCHIVED-v4.x`，`CLAUDE.md` 为唯一真相源 |
| 0.3 | 更新 `CLAUDE.md` 反映 v5.1 实际结构 | `CLAUDE.md` | 文件路径、行数、命令与实际代码一致 |

### 阶段 1：核心基础设施（本周，3-5 天）

#### 1.1 ExecutionTrace 状态机（P0）

**目标**：替代文件存在性检查，实现真正的执行完整性。

```python
# 新增 noteforge/infra/execution_trace.py
class ExecutionTrace:
    """流水线执行追踪，支持断点恢复和完整性验证"""
    
    class Status(Enum):
        PENDING = "pending"      # 未开始
        RUNNING = "running"      # 进行中（含开始时间）
        COMPLETED = "completed"  # 完成（含输出哈希）
        FAILED = "failed"        # 失败（含错误类型和重试次数）
        DEAD_LETTER = "dead_letter"  # 永久放弃
    
    @dataclass
    class StepRecord:
        stage: str              # 阶段名（download/transcribe/preprocess/generate/format/evaluate/sync）
        status: Status
        input_hash: str         # 输入内容 SHA-256
        output_hash: Optional[str]  # 输出内容 SHA-256（COMPLETED 时必填）
        started_at: datetime
        completed_at: Optional[datetime]
        error_type: Optional[str]   # TRANSIENT / PERMANENT / DEGRADED
        retry_count: int = 0
        
    # 原子写：先写 .tmp，再重命名
    def save(self, trace_id: str, records: list[StepRecord]):
        ...
    
    # 恢复时验证哈希链完整性
    def resume(self, trace_id: str) -> list[StepRecord]:
        ...
```

**验收标准**：
- [ ] `kill -9` 中断 LLM 调用后，下次 `--resume` 能检测部分文件，从最后有效阶段重处理
- [ ] 能区分 "transcribed but not generated" vs "generated but quality gate failed"
- [ ] checkpoint 文件损坏（JSON 截断）时有降级恢复策略

#### 1.2 ASRProvider ABC + 健康检查（P0）

**目标**：解耦 ASR 实现，支持 CI/mock/云端回退。

```python
# 新增 noteforge/sources/asr_provider.py
class ASRProvider(ABC):
    @abstractmethod
    def health_check(self) -> tuple[bool, str]: ...  # (健康, 诊断信息)
    
    @abstractmethod
    def transcribe(self, audio_path: str, timeout: int = 7200) -> TranscriptionResult: ...
    
    @property
    @abstractmethod
    def name(self) -> str: ...

class LocalParaformerASR(ASRProvider):
    """当前 FunASR 实现"""
    
class MockASR(ASRProvider):
    """CI/测试用，返回固定文本"""
    
class CloudASR(ASRProvider):
    """云端回退（v5.1 实现）"""
```

**CLI 新增**：
```bash
python -m noteforge --health-check          # 验证所有组件
python -m noteforge --health-check --asr    # 仅验证 ASR
```

**验收标准**：
- [ ] `noteforge --health-check` 验证：Python 环境、ASR 依赖、LLM 可达性、飞书模块可导入
- [ ] CI 无需安装 3GB torch 即可运行 ASR 相关测试（使用 MockASR）
- [ ] ASR 子进程 2h 超时后 SIGTERM→SIGKILL 升级

#### 1.3 失败模式分类策略（P1）

**目标**：按异常类型定义处理策略，替代统一的 `logger.debug/warning`。

```python
# 新增 noteforge/infra/failure_policy.py
class FailurePolicy(Enum):
    TRANSIENT = "transient"    # 退避重试（网络超时、429）
    PERMANENT = "permanent"    # 停止流水线（配置错误、凭证失效）
    DEGRADED = "degraded"      # 带标注继续（转写质量差、部分概念缺失）
    SKIP = "skip"              # 跳过当前项（视频已删除、转写过短）

# 分类器
class FailureClassifier:
    def classify(self, exception: Exception, context: dict) -> FailurePolicy:
        ...
```

**验收标准**：
- [ ] `FileNotFoundError` on temp cleanup → continue (SKIP)
- [ ] `UnicodeDecodeError` on transcript → halt (PERMANENT)
- [ ] `LLMRateLimitError` → retry with backoff (TRANSIENT)
- [ ] `QualityGateFailure` → human review queue (DEGRADED)

#### 1.4 超时与熔断机制（P0）

| 操作 | 单项超时 | 批量总体 | 升级策略 |
|------|---------|---------|---------|
| 下载 | ≤30min | ≤12h | SIGTERM→SIGKILL |
| ASR 转写 | ≤2h | — | 同上 |
| LLM 生成 | ≤10min | — | 同上 |
| 质量门禁 | ≤5min | — | 同上 |
| 飞书同步 | ≤5min | — | 同上 |

**验收标准**：
- [ ] mock 3h sleep 的 ASR 子进程 → 超时杀死、记录、继续下一项
- [ ] 连续 3 次 LLM 失败后批量停止并告警

### 阶段 2：CLI 命令补全（下周，3-5 天）

#### 2.1 环境管理命令

```bash
# 新增命令
python -m noteforge setup              # 一键创建 envs/paraformer/，安装依赖
python -m noteforge doctor             # 诊断环境，输出缺失项和修复建议
python -m noteforge validate-config    # 验证 YAML 配置完整性和有效性
```

**验收标准**：
- [ ] `noteforge setup` 在新机器上成功创建隔离环境
- [ ] `noteforge doctor` 检测缺失的：Python 3.10、yt-dlp、ffmpeg、lark-cli、.env 文件
- [ ] `noteforge validate-config` 捕获：缺失必填字段、无效 YAML、凭证格式错误

#### 2.2 批量处理增强

```bash
# 现有命令增强
python -m noteforge --batch --resume          # 断点续传（集成 auto_pipeline 能力）
python -m noteforge --batch --checkpoint-file path.json  # 自定义进度文件
python -m noteforge --batch --dry-run         # 预览模式（不调用 LLM，只打印计划）

# 新增命令
python -m noteforge progress --show           # 查看当前进度
python -m noteforge progress --clear          # 清除进度
```

**验收标准**：
- [ ] `main.py` 的 `run_batch()` 支持 `--resume`
- [ ] 进度文件格式与 `auto_pipeline` 兼容或统一
- [ ] `--dry-run` 模式下跳过 LLM 调用和文件写入，但显示完整执行计划

#### 2.3 质量系统 CLI

```bash
# 新增命令
python -m noteforge quality-view <note_file>   # 查看质量报告
python -m noteforge quality-list                 # 列出所有质量报告
python -m noteforge --check-only <file> --format json|md|table  # 多格式输出
python -m noteforge --check-only <file> --verbose  # 显示所有规则结果

# 参数增强
python -m noteforge --input ep01 --min-score 0.75 --max-retries 5  # 临时覆盖阈值
```

**验收标准**：
- [ ] `--format json` 输出结构化 JSON
- [ ] `--format md` 输出可读 Markdown
- [ ] `--min-score` 单次运行覆盖 YAML 配置

#### 2.4 飞书同步增强

```bash
# 新增命令
python -m noteforge feishu-auth        # 引导 lark-cli 认证
python -m noteforge feishu-validate  # 验证凭证和连接

# 凭证管理
# 将 space_id / root_node_token 从 YAML 迁移到 .env
```

**验收标准**：
- [ ] `feishu-auth` 检测 lark-cli 是否安装，引导认证流程
- [ ] `feishu-validate` 验证 token 有效性、space 可访问性
- [ ] 凭证从 YAML 迁移到 `.env`，YAML 保留空值回退

#### 2.5 知识合成 CLI

```bash
# 新增命令
python -m noteforge detect-domain <file>       # 检测知识域
python -m noteforge incremental-update --domain <id>  # 增量合成
python -m noteforge domain-list                # 列出知识域
```

#### 2.6 其他便利命令

```bash
python -m noteforge providers          # 查看 LLM Provider 状态
python -m noteforge cleanup --logs --temp --extractions  # 清理临时文件
```

### 阶段 3：架构优化（第 3 周，3-5 天）

#### 3.1 PipelineContext 拆分（P1）

**目标**：按 stage 显式依赖，消除 12 字段 shotgun。

```python
# 当前（反模式）
class PipelineContext:
    content_type: str
    output_dir: Path
    feishu_credentials: dict  # GenerateStage 不需要
    domain_classifier: DomainClassifier  # FormatStage 不需要
    ...

# 目标
class GenerateStage:
    def __init__(self, content_type: str, provider: LLMProvider, rules: list): ...
    
class FormatStage:
    def __init__(self, content_type: str, formatter: NoteFormatter): ...
```

**验收标准**：
- [ ] 静态分析：`grep -r "context\." noteforge/engine/stages/` 只匹配 `__init__` 参数
- [ ] 每个 stage 只接收其需要的依赖

#### 3.2 配置不可变性（P2）

**目标**：CLI 边界加载一次，传递只读视图。

```python
# 新增 frozen dataclass
@dataclass(frozen=True)
class EngineConfig:
    min_score: float
    max_retries: int
    provider: ProviderConfig
    ...

# CLI 边界加载
config = NoteForgeConfig().freeze()  # 返回 EngineConfig
engine = LLMNoteEngine(config)
```

**验收标准**：
- [ ] 混沌测试：批量执行中修改 `llm_engine_config.yaml` → 不影响运行流水线
- [ ] `config_hash` 写入 ExecutionTrace

#### 3.3 依赖注入（P1）

**目标**：`engine.__init__` 不再直接 import/实例化子系统。

```python
# 当前
class LLMNoteEngine:
    def __init__(self):
        self.provider = create_provider()  # 硬编码
        self.classifier = DomainClassifier()  # 硬编码
        ...

# 目标
class LLMNoteEngine:
    def __init__(
        self,
        provider: LLMProvider,
        classifier: DomainClassifier,
        quality_manager: QualityManager,
        ...
    ):
        ...
```

### 阶段 4：测试与验证（第 4 周，3-5 天）

#### 4.1 覆盖率基线

```bash
pytest --cov=noteforge --cov-branch --cov-report=term-missing
```

**目标**：建立基线，协商 `--cov-fail-under` 阈值（建议 70% 起步）。

#### 4.2 集成测试（关键）

| 场景 | 测试方法 | 验证点 |
|------|---------|--------|
| ASR 子进程挂起 | mock 3h sleep subprocess | 超时杀死、重试、记录 |
| LLM 429 重试 | mock HTTP 429 | 指数退避、最大重试、最终失败 |
| kill -9 恢复 | 实际 `kill -9` 注入 | checkpoint 完整性、恢复行为 |
| 配置运行时修改 | 批量执行中修改 YAML | 不影响运行流水线 |
| 并发 JSON 访问 | 多线程读写 video-mapping.json | 无数据损坏 |

#### 4.3 混沌测试

```bash
# 新增脚本 tests/chaos/
python tests/chaos/test_kill9_resume.py      # kill -9 每阶段注入
python tests/chaos/test_disk_full.py           # 磁盘满场景
python tests/chaos/test_api_key_revoke.py      # API key 撤销
```

### 阶段 5：文档与运维（持续）

#### 5.1 Runbook

| 文档 | 内容 | 负责人 |
|------|------|--------|
| `RUNBOOK-auto-pipeline-failure.md` | 5 分钟回滚流程 | DevOpsEngineer |
| `RUNBOOK-asr-recovery.md` | ASR 环境崩溃恢复 | SystemArchitect |
| `RUNBOOK-feishu-auth.md` | 飞书认证故障排查 | DevOpsEngineer |

#### 5.2 监控与告警

```python
# 新增 noteforge/infra/monitoring.py
class PipelineMonitor:
    def heartbeat(self, stage: str, item_id: str): ...
    def alert_on_stall(self, timeout: int = 300): ...
    def report_completion(self, stats: dict): ...
```

---

## 三、文件变更清单

### 新增文件

```
noteforge/
  infra/
    execution_trace.py       # ExecutionTrace 状态机
    failure_policy.py          # 失败模式分类
    monitoring.py              # 监控与告警
  sources/
    asr_provider.py            # ASRProvider ABC
    asr_local.py               # LocalParaformerASR 实现
    asr_mock.py                # MockASR 实现
  cli/
    commands/
      setup.py                 # noteforge setup
      doctor.py                # noteforge doctor
      validate_config.py       # noteforge validate-config
      progress.py              # noteforge progress
      quality_view.py          # noteforge quality-view
      feishu_auth.py           # noteforge feishu-auth
      cleanup.py               # noteforge cleanup
      domain.py                # noteforge detect-domain / domain-list
  tests/
    chaos/
      test_kill9_resume.py
      test_disk_full.py
      test_api_key_revoke.py
    integration/
      test_asr_timeout.py
      test_llm_retry.py
      test_checkpoint_recovery.py
```

### 修改文件

```
noteforge/
  cli/
    main.py                    # 新增命令参数、--health-check、--dry-run、--resume
    commands/
      __init__.py              # 导出新命令
      check.py                 # 支持 --format、--verbose
      batch_cmd.py             # 集成 resume、checkpoint
  batch/
    auto_pipeline.py           # 迁移到 ExecutionTrace
    processor.py               # 进度持久化
  engine/
    note_engine.py             # 依赖注入、配置不可变性
    pipeline.py                # 集成 ExecutionTrace
    stages/
      base.py                  # 显式依赖接口
      generate.py              # 失败模式分类
      preprocess.py            # 超时机制
  core/
    llm_providers.py           # health_check() 方法
  integration/
    feishu.py                  # 认证错误检测、引导
    feishu_sync.py             # 凭证从 YAML→.env
  config.py                    # frozen config、验证
  infra/
    env.py                     # 扩展检查项
```

### 删除/归档文件

```
docs/refactoring-phases-2-3-4.md   → ARCHIVED-v4.x
docs/refactoring-roadmap.md        → ARCHIVED-v4.x
```

---

## 四、验收标准汇总

### 阶段 0
- [ ] `pytest --cov=noteforge` 输出基线覆盖率
- [ ] `docs/` 无未实现计划，`CLAUDE.md` 与实际代码一致

### 阶段 1
- [ ] `kill -9` 后 `--resume` 正确恢复
- [ ] `noteforge --health-check` 通过
- [ ] CI 无需 torch 运行 ASR 测试
- [ ] 超时机制：mock 3h sleep → 杀死、重试、继续
- [ ] 失败分类：每种异常类型有明确定义策略

### 阶段 2
- [ ] `noteforge setup` 新机器可用
- [ ] `noteforge doctor` 检测所有缺失项
- [ ] `noteforge --batch --resume` 可用
- [ ] `noteforge --batch --dry-run` 预览正确
- [ ] `noteforge quality-view` 可读
- [ ] `noteforge feishu-auth` 引导认证
- [ ] 凭证从 YAML 迁移到 `.env`

### 阶段 3
- [ ] PipelineContext 按 stage 拆分，静态分析通过
- [ ] 配置运行时修改不影响批量流水线
- [ ] engine.__init__ 使用构造函数注入

### 阶段 4
- [ ] 覆盖率 ≥ 70%（协商后基线）
- [ ] 集成测试：ASR 挂起、LLM 429、kill-9 恢复
- [ ] 混沌测试通过

### 阶段 5
- [ ] Runbook 文档完成
- [ ] 监控心跳机制可用

---

## 五、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| ExecutionTrace 引入循环导入 | 中 | 高 | 先画依赖图，models 作为叶子节点 |
| PipelineContext 拆分影响现有测试 | 中 | 中 | 每步端到端验证，保留兼容接口 |
| ASRProvider ABC 延迟交付 | 低 | 高 | 先实现 MockASR，LocalParaformerASR 后续迁移 |
| 凭证迁移导致飞书同步中断 | 低 | 高 | 保留 YAML 空值回退，渐进迁移 |
| 超时机制误杀长视频 ASR | 中 | 中 | ASR 超时 2h 足够覆盖 99% 场景，超长视频手动处理 |

---

## 六、时间线

| 周 | 阶段 | 核心交付 |
|----|------|---------|
| W1 | 阶段 0 + 1.1 | 覆盖率基线、归档文档、ExecutionTrace |
| W2 | 阶段 1.2-1.4 | ASRProvider、健康检查、超时熔断、失败分类 |
| W3 | 阶段 2 | CLI 命令补全（setup/doctor/resume/dry-run/quality-view/feishu-auth） |
| W4 | 阶段 3 | PipelineContext 拆分、配置不可变性、依赖注入 |
| W5 | 阶段 4 | 覆盖率 ≥70%、集成测试、混沌测试 |
| W6 | 阶段 5 | Runbook、监控、最终验收 |

---

*本计划为 Council 评审结论的完整实现路线图。每个阶段独立可交付，完成后更新此文件状态。*
