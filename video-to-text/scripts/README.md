# scripts/ — 已迁移

此目录下的所有脚本已迁移到 `noteforge/` 包内。

| 旧路径 | 新路径 | 入口命令 |
|--------|--------|---------|
| `scripts/cli.py` | `noteforge/cli/main.py` | `python -m noteforge` |
| `scripts/paraformer_transcribe.py` | `noteforge/sources/asr.py` | `python -m noteforge.sources.asr` |
| `scripts/llm_note_engine.py` | `noteforge/engine/note_engine.py` | — |
| `scripts/quality_gate.py` | `noteforge/quality/gate.py` + `report.py` | — |
| 其他 | 对应 `noteforge/` 子包 | — |

新代码请使用 `from noteforge.xxx import yyy`。
