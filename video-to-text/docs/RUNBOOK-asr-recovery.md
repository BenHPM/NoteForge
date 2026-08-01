# ASR Environment Recovery Runbook

## Symptoms

| Symptom | Likely Cause |
|---------|-------------|
| `ModuleNotFoundError: funasr` | Python 3.10 env not activated or funasr not installed |
| `FileNotFoundError: model directory` | Model cache missing or path misconfigured |
| `CUDA out of memory` | GPU VRAM insufficient for audio length |
| `RuntimeError: No CUDA GPU` | CUDA driver missing or GPU unavailable |
| ASR returns empty string | Audio file corrupt, too short, or wrong format |
| `ASRTimeoutError` | Audio too long, model hung, or system overloaded |

## Diagnosis

### Step 1: ASR Health Check

```bash
envs\paraformer\python.exe -m noteforge --health-check-asr
```

Checks: Paraformer venv, FunASR, PyTorch, ffmpeg. Reports OK/FAIL per component.

### Step 2: Verify Python 3.10 Environment

```bash
envs\paraformer\python.exe --version
# Expected: Python 3.10.x

envs\paraformer\python.exe -c "import funasr; print(funasr.__version__)"
envs\paraformer\python.exe -c "import torch; print(torch.__version__)"
```

### Step 3: Check CUDA Availability

```bash
envs\paraformer\python.exe -c "import torch; print('CUDA:', torch.cuda.is_available()); print('Device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

### Step 4: Full Environment Check

```bash
envs\paraformer\python.exe -m noteforge --doctor
```

## Recovery

### Fix 1: Reinstall ASR Environment (--setup)

```bash
cd video-to-text
envs\paraformer\python.exe -m noteforge --setup
```

Or manually:

```bash
py -3.10 -m venv envs\paraformer
envs\paraformer\Scripts\activate
pip install -r requirements.txt
```

### Fix 2: Reinstall FunASR/PyTorch Only

```bash
envs\paraformer\Scripts\activate
pip install -r requirements-asr.txt --extra-index-url https://download.pytorch.org/whl/cpu
```

### Fix 3: Clear and Re-download Model Cache

```bash
rmdir /s /q "%USERPROFILE%\.cache\modelscope\hub\iic\speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"

# Re-run ASR to trigger download
envs\paraformer\python.exe -m noteforge.sources.asr ep01
```

### Fix 4: Use CPU Mode (CUDA OOM Workaround)

```bash
set CUDA_VISIBLE_DEVICES=""
envs\paraformer\python.exe -m noteforge --input ep01
```

### Fix 5: Split Long Audio (>2h)

```bash
ffmpeg -i input.mp3 -t 3600 -c copy part1.mp3
ffmpeg -i input.mp3 -ss 3600 -c copy part2.mp3
```

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: funasr` | funasr not in current env | Activate `envs\paraformer` or run `--setup` |
| `ModuleNotFoundError: torch` | PyTorch not installed | `pip install torch --extra-index-url https://download.pytorch.org/whl/cpu` |
| `FileNotFoundError: .../model.pb` | Model cache incomplete | Delete cache dir, re-run to download |
| `RuntimeError: CUDA out of memory` | Audio too long for GPU VRAM | Use CPU mode (`set CUDA_VISIBLE_DEVICES=""`) or split audio |
| `RuntimeError: No CUDA GPU` | No GPU or driver issue | Install CUDA drivers or use CPU mode |
| `ASRTimeoutError` | Model hung or system slow | Check system load, increase timeout in config |
| `UnicodeDecodeError` | Audio file path has special chars | Rename file to ASCII-only path |
| Empty transcript | Audio <1s or wrong format | Check audio file with `ffprobe` |
| `ConnectionError` during model download | No internet for model download | Use pre-downloaded model or proxy |
| Paraformer venv not found | venv not created | Run `envs\paraformer\python.exe -m noteforge --setup` |
