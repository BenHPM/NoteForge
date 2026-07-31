# RUNBOOK: ASR Environment Recovery

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

### Step 1: Health Check

```bash
envs\paraformer\python.exe -m noteforge --health-check-asr
```

This runs the ASR provider health check and reports status.

### Step 2: Verify Python 3.10 Environment

```bash
# Check Python version in isolated env
envs\paraformer\python.exe --version
# Expected: Python 3.10.x

# Check funasr is installed
envs\paraformer\python.exe -c "import funasr; print(funasr.__version__)"
```

### Step 3: Check Model Cache

```bash
# Default model cache location
dir "%USERPROFILE%\.cache\modelscope\hub\iic\"

# Or check via Python
envs\paraformer\python.exe -c "from funasr import AutoModel; print('OK')"
```

### Step 4: Check CUDA Availability

```bash
envs\paraformer\python.exe -c "import torch; print('CUDA:', torch.cuda.is_available()); print('Device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

## Recovery

### Fix 1: Reinstall ASR Environment

```bash
# Recreate isolated environment
cd video-to-text
py -3.10 -m venv envs\paraformer
envs\paraformer\Scripts\activate
pip install -r requirements.txt
```

### Fix 2: Clear and Re-download Model Cache

```bash
# Remove corrupted model cache
rmdir /s /q "%USERPROFILE%\.cache\modelscope\hub\iic\speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"

# Re-run ASR to trigger download
envs\paraformer\python.exe -m noteforge.sources.asr --test
```

### Fix 3: Use CPU Mode (CUDA OOM Workaround)

```bash
# Force CPU inference
set CUDA_VISIBLE_DEVICES=""
envs\paraformer\python.exe -m noteforge --input ep01
```

### Fix 4: Use MockASR for CI/Testing

```bash
# Set environment variable to use MockASR
set NOTEFORGE_TEST=1
envs\paraformer\python.exe -m noteforge --input ep01

# Or for CI
set CI=true
envs\paraformer\python.exe -m pytest tests/ -v
```

### Fix 5: Split Long Audio

```bash
# If audio >2h, split with ffmpeg before ASR
ffmpeg -i input.mp3 -t 3600 -c copy part1.mp3
ffmpeg -i input.mp3 -ss 3600 -c copy part2.mp3
```

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: funasr` | funasr not in current env | Activate `envs\paraformer` or reinstall |
| `ModuleNotFoundError: torch` | PyTorch not installed | `pip install torch` in paraformer env |
| `FileNotFoundError: .../model.pb` | Model cache incomplete | Delete cache dir, re-run to download |
| `RuntimeError: CUDA out of memory` | Audio too long for GPU VRAM | Use CPU mode or split audio |
| `RuntimeError: No CUDA GPU` | No GPU or driver issue | Install CUDA drivers or use CPU mode |
| `ASRTimeoutError` | Model hung or system slow | Check system load, increase timeout in config |
| `UnicodeDecodeError` | Audio file path has special chars | Rename file to ASCII-only path |
| Empty transcript | Audio <1s or wrong format | Check audio file with `ffprobe` |
| `ConnectionError` during model download | No internet for model download | Use pre-downloaded model or proxy |
