# Auto Pipeline Failure Recovery Runbook

## Symptoms

| Symptom | Likely Cause |
|---------|-------------|
| Pipeline hangs (no output >5 min) | LLM API timeout, ASR stall, network hang |
| Pipeline crashes with traceback | Unhandled exception (config error, OOM, missing file) |
| Empty output file (0 bytes) | LLM returned empty, quality gate rejected all retries |
| `pipeline_progress.json` not updating | Process died without cleanup, disk full |
| Circuit breaker OPEN in logs | 3+ consecutive LLM/ASR failures |
| `DEAD_LETTER` status in progress | URL failed 3+ times across runs, permanently skipped |

## Diagnosis

### Step 1: Check Progress File

```bash
type output\logs\pipeline_progress.json
```

Look for: `status: failed` (resumable), `dead_letter` (permanently skipped), `success` (done).

### Step 2: Check ExecutionTrace

```bash
dir /o-d output\logs\traces\
type output\logs\traces\TRACE_ID.json
```

- `DEAD_LETTER` = permanent failure, not resumable
- `FAILED` = may be resumable from last `COMPLETED` stage
- Hash chain break = input changed since last run

### Step 3: Check Pipeline Logs

```bash
findstr /i "error failed exception" output\logs\noteforge.log
```

### Step 4: Check Circuit Breaker

```bash
findstr "Circuit" output\logs\noteforge.log
```

If OPEN, wait for recovery timeout or restart pipeline.

### Step 5: Full Environment Check

```bash
envs\paraformer\python.exe -m noteforge --doctor
```

## Recovery Steps

### Option A: Resume from Checkpoint (preferred)

```bash
# Resume auto pipeline from last checkpoint
envs\paraformer\python.exe -m noteforge.batch.auto_pipeline urls.txt --resume

# Resume batch processing via CLI
envs\paraformer\python.exe -m noteforge --batch --resume
```

### Option B: Single Item Retry

```bash
# Retry a single failed item (force overwrite)
envs\paraformer\python.exe -m noteforge --input ep01 --force
```

### Option C: Clear Stuck Progress

```bash
# Clear progress via CLI
envs\paraformer\python.exe -m noteforge progress --clear

# Or manually delete
del output\logs\pipeline_progress.json
```

### Option D: Reset Dead Letters

Dead-letter URLs are permanently skipped. To retry them, edit the progress file:

```bash
# Find dead-letter entries
findstr "dead_letter" output\logs\pipeline_progress.json

# Edit file and change "dead_letter" to "pending", then resume
envs\paraformer\python.exe -m noteforge.batch.auto_pipeline urls.txt --resume
```

## Rollback (5-minute procedure)

1. **Identify last good output**:
   ```bash
   dir /o-d output\notes\
   ```

2. **Remove corrupted output**:
   ```bash
   del output\notes\BROKEN_FILE.md
   del output\quality_reports\BROKEN_FILE.json
   ```

3. **Clear the trace for the failed item**:
   ```bash
   del output\logs\traces\TRACE_ID.json
   ```

4. **Reset progress if needed**:
   ```bash
   del output\logs\pipeline_progress.json
   ```

5. **Re-run from last known good state**:
   ```bash
   envs\paraformer\python.exe -m noteforge --batch --resume
   ```

## Escalation

Contact maintainers when:
- `DEAD_LETTER` on 3+ items in a single run
- Circuit breaker stays OPEN after 10+ minutes
- `--doctor` reports ASR or LLM as unhealthy and local recovery fails
- Disk space under 1GB (can cause silent write failures)
- Repeated hash chain breaks suggest config drift or file corruption
