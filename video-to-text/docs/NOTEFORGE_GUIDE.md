# 🔨 NoteForge - 智能笔记锻造系统

> **状态**: ✅ 已部署 | **引擎**: Paraformer (FunASR)  
> **优势**: 比 Whisper 快 4-5 倍 | **中文准确率**: 96%+  
> **版本**: v3.0 (含质量控制系统)

---

## 📁 项目结构

```
D:\ProgramData\TraeCN\zmt-os\video-to-text\
├── envs/
│   └── paraformer/              ← Python 3.10 独立环境
├── scripts/
│   ├── paraformer_transcribe.py ← 主转写脚本
│   ├── test_paraformer.py       ← 环境验证工具
│   └── quality_gate.py          ← 笔记质量评分引擎 ⭐
├── config/
│   ├── video-mapping.json       ← 视频配置文件
│   ├── note_generation_rules.yaml ← 笔记生成规则 ⭐
│   └── experience_log.yaml      ← 经验积累日志 ⭐
├── noteforge.bat                ← 一键启动 ⭐
└── output/
    └── transcripts/             ← 转写结果输出
```

---

## 🎯 快速开始

### 方法一：双击运行（推荐）

```
双击: D:\ProgramData\TraeCN\zmt-os\video-to-text\noteforge.bat
```

### 方法二：命令行

```powershell
# 转写单个视频
$py = "D:\ProgramData\TraeCN\zmt-os\video-to-text\envs\paraformer\python.exe"
& $py "D:\ProgramData\TraeCN\zmt-os\video-to-text\scripts\paraformer_transcribe.py" video.mp4

# 批量转写目录
& $py "...scripts\paraformer_transcribe.py" D:\videos\
```

---

## 🔧 环境配置

### Python 环境

- **路径**: `D:\ProgramData\TraeCN\zmt-os\video-to-text\envs\paraformer\python.exe`
- **版本**: Python 3.10.11
- **类型**: 独立环境（不影响系统Python）

### 核心依赖

| 包名 | 版本 | 用途 |
|------|------|------|
| torch | 2.2.0+cpu | 深度学习框架 |
| funasr | 1.3.1 | Paraformer 模型 |
| modelscope | - | 模型下载管理 |
| onnxruntime | 1.23.2 | 高效推理 |
| soundfile | - | 音频处理 |

---

## ✅ 验证环境

```powershell
$py = "D:\ProgramData\TraeCN\zmt-os\video-to-text\envs\paraformer\python.exe"

# 测试1: Python 版本
& $py --version
# 输出: Python 3.10.11

# 测试2: FunASR 导入
& $py -c "from funasr import AutoModel; print('OK')"
# 输出: OK

# 测试3: 完整功能测试
& $py "D:\ProgramData\TraeCN\zmt-os\video-to-text\scripts\test_paraformer.py"
# 输出: ✅ NoteForge 环境就绪！
```

---

## 🎬 使用示例

### 示例1：转写单个视频

```powershell
$py = "D:\ProgramData\TraeCN\zmt-os\video-to-text\envs\paraformer\python.exe"
& $py "...\scripts\paraformer_transcribe.py" "D:\videos\lecture.mp4"
```

**输出位置**: 同目录下生成 `lecture.txt`

### 示例2：批量转写

```powershell
& $py "...\scripts\paraformer_transcribe.py" all
```

自动扫描配置文件中的所有视频并转写。

### 示例3：添加热词（提升专业术语准确率）

编辑脚本中的参数：

```python
result = model.generate(
    input=audio_path,
    batch_size_s=300,
    hotword='量化投资 超额收益 因子选股'  # 添加领域词汇
)
```

---

## 📊 性能参考

基于实际测试结果（CPU模式）：

| 视频时长 | 处理时间 | RTF | 字符数 |
|---------|---------|-----|--------|
| 70分钟 | ~15分钟 | 0.22x | 23,341 |
| 96分钟 | ~27分钟 | 0.28x | 24,431 |

**说明**: RTF < 1 表示比实时快。NoteForge 平均比实时快 **4倍**。

---

## 🆘 常见问题

**Q: 首次运行很慢？**  
A: 正常！首次需下载模型（~800MB），后续会使用缓存。

**Q: 如何提升准确率？**  
A: 
- 添加领域热词（`hotword` 参数）
- 确保音频质量清晰（16kHz以上采样率）

**Q: 内存占用过高？**  
A: 默认已优化。长视频建议分段处理（脚本已内置此功能）。

**Q: 支持哪些格式？**  
A: 输入支持 MP4/MKV/AVI/MOV 等（通过FFmpeg转换）。输出为 TXT 格式。

**Q: 如何更新模型？**  
A: 删除缓存后重新运行即可：
```powershell
Remove-Item "$env:USERPROFILE\.cache\modelscope" -Recurse -Force
```

---

## 🛡️ 笔记质量控制系统

为确保生成的笔记准确、可靠，NoteForge 集成了**质量评分引擎**，包含以下核心组件：

### 规则体系（note_generation_rules.yaml）

| 规则ID | 名称 | 严重度 | 说明 |
|--------|------|--------|------|
| R1 | 禁止虚构数据 | 🔴 致命 | 数字、百分比必须在原文有确切出处 |
| R2 | 禁止越界增补 | 🔴 致命 | 不得添加原文未说的话 |
| R3 | 禁止事实反转 | 🔴 致命 | 不得反转原文语义 |
| R4 | 禁止概念失真 | 🟡 严重 | 不得扭曲专业术语含义 |
| R5 | 覆盖度底线 | 🟡 严重 | 核心内容覆盖率需≥60% |
| R6 | 术语一致性 | 🟢 一般 | 保持术语前后使用统一 |

### 评分权重

```
R1 (禁止虚构数据): 25%
R2 (禁止越界增补): 20%
R3 (禁止事实反转): 25%
R4 (禁止概念失真): 15%
R5 (覆盖度底线): 10%
R6 (术语一致性): 5%
```

### 使用方法

```powershell
$py = "D:\ProgramData\TraeCN\zmt-os\video-to-text\envs\paraformer\python.exe"

# 评估笔记质量
& $py "D:\ProgramData\TraeCN\zmt-os\video-to-text\scripts\quality_gate.py" `
    -n "笔记路径\STUDY_NOTES.md" `
    -s "原文路径\transcript.txt"
```

### 经验积累（experience_log.yaml）

系统会记录历史错误案例，包括：
- 错误类型和原文引用
- 根因分析
- 衍生规则

持续迭代优化，确保同类错误不再重复。

---

## 📞 技术支持

遇到问题时，请提供：

1. 错误信息截图
2. 视频文件信息（大小、时长、格式）
3. 执行的完整命令

---

**最后更新**: 2026-05-09  
**当前版本**: v3.0 (NoteForge - 含质量控制系统)