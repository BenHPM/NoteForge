# 🔨 NoteForge

> **智能笔记锻造系统** - 将视频内容自动转写为高质量笔记

[![Status](https://img.shields.io/badge/status-Ready-brightgreen)](https://github.com/BenHPM/short-video-director-course)
[![Engine](https://img.shields.io/badge/engine-Paraformer%20(FunASR)-blue)](https://github.com/modelscope/FunASR)
[![Accurate](https://img.shields.io/badge/accuracy-96%25+-yellow)](https://github.com/BenHPM/short-video-director-course)

---

## ✨ 核心特性

- 🎬 **视频转文本**: 基于阿里 Paraformer 模型，中文准确率 96%+
- ⚡ **高效快速**: 比 Whisper 快 4-5 倍，RTF < 0.3
- 🛡️ **质量控制**: 内置 6 维质量评分引擎，确保笔记准确可靠
- 📚 **经验积累**: 自动记录错误案例，持续优化生成质量
- 🔧 **易于部署**: 一键启动，Windows 原生支持

---

## 📁 项目结构

```
video-to-text/
├── config/
│   ├── experience_log.yaml          # 经验积累日志
│   ├── note_generation_rules.yaml  # 笔记生成质量规则
│   └── video-mapping.json          # 视频配置
├── docs/
│   └── NOTEFORGE_GUIDE.md         # 完整使用指南
├── envs/
│   └── paraformer/                # Python 3.10 独立环境
├── scripts/
│   ├── paraformer_transcribe.py   # 视频转写脚本
│   ├── test_paraformer.py          # 环境验证工具
│   └── quality_gate.py             # 质量评分引擎
├── noteforge.bat                   # ⭐ 一键启动
└── output/
    ├── notes/                      # 生成的笔记
    └── transcripts/                # 转写文本
```

---

## 🚀 快速开始

### 方法一：双击运行（推荐）

```
双击: video-to-text\noteforge.bat
```

### 方法二：命令行

```powershell
# 设置 Python 路径
$py = "D:\ProgramData\TraeCN\zmt-os\video-to-text\envs\paraformer\python.exe"

# 转写单个视频
& $py "video-to-text\scripts\paraformer_transcribe.py" video.mp4

# 批量转写
& $py "video-to-text\scripts\paraformer_transcribe.py" all
```

---

## 🛡️ 质量控制系统

NoteForge 内置 6 维质量评分引擎，确保生成的笔记准确可靠：

| 规则 | 名称 | 权重 | 说明 |
|------|------|------|------|
| R1 | 禁止虚构数据 | 25% | 数字、百分比必须有原文出处 |
| R2 | 禁止越界增补 | 20% | 不得添加原文未说的话 |
| R3 | 禁止事实反转 | 25% | 不得反转原文语义 |
| R4 | 禁止概念失真 | 15% | 不得扭曲专业术语 |
| R5 | 覆盖度底线 | 10% | 核心内容覆盖≥60% |
| R6 | 术语一致性 | 5% | 保持术语统一 |

### 使用质量评分

```powershell
$py = "video-to-text\envs\paraformer\python.exe"
& $py "video-to-text\scripts\quality_gate.py" `
    -n "笔记路径\STUDY_NOTES.md" `
    -s "原文路径\transcript.txt"
```

---

## 📊 性能参考

| 视频时长 | 处理时间 | RTF | 字符数 |
|---------|---------|-----|--------|
| 70分钟 | ~15分钟 | 0.22x | 23,341 |
| 96分钟 | ~27分钟 | 0.28x | 24,431 |

**RTF < 1** 表示比实时快，NoteForge 平均比实时快 **4倍**。

---

## 🔧 环境要求

- **系统**: Windows 10/11
- **Python**: 3.10.11 (独立环境，不影响系统)
- **依赖**: torch, funasr, modelscope, onnxruntime, soundfile

---

## 📖 详细文档

请参考 [NOTEFORGE_GUIDE.md](video-to-text/docs/NOTEFORGE_GUIDE.md) 获取完整使用说明。

---

## 📝 笔记生成规则

NoteForge 采用以下规则确保笔记质量：

### R1 - 禁止虚构数据
所有数字、百分比、权重、排名、金额**必须在原文中有确切出处**。原文仅为定性描述时，不得自行量化。

### R2 - 禁止越界增补
不得添加原文嘉宾/主持人**未说过的话**、未给出的建议、未发表的观点。如需补充背景知识，必须用明确标记区分来源。

### R3 - 禁止事实反转
**不得反转原文语义**，将肯定表述改为否定，或将否定改为肯定。

### R4 - 禁止概念失真
不得扭曲专业术语的原本含义，特别是金融、技术等行业术语。

### R5 - 覆盖度底线
核心内容覆盖率需达到 **60%以上**，确保笔记完整性。

### R6 - 术语一致性
同一术语在笔记中需**保持前后统一**，不得混用不同名称。

---

## 🐛 常见问题

**Q: 首次运行很慢？**
A: 正常！首次需下载模型（~800MB），后续使用缓存。

**Q: 如何提升准确率？**
A: 使用 `hotword` 参数添加领域专业词汇。

**Q: 支持哪些视频格式？**
A: 输入支持 MP4/MKV/AVI/MOV 等（通过 FFmpeg 转换）。

---

## 📜 许可证

本项目仅供个人学习研究使用。

---

**最后更新**: 2026-05-09
**版本**: v3.0 (NoteForge)
