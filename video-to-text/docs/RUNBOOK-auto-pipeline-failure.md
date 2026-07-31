# RUNBOOK: Auto Pipeline Failure Recovery

## Symptoms

| Symptom | Likely Cause |
|---------|-------------|
| Pipeline hangs (no output for >5 min) | LLM API timeout, ASR stall, network hang |
| Pipeline crashes with traceback | Unhandled exception (config error, OOM, missing file) |
| Empty output file (0 bytes) | LLM returned empty, quality gate rejected all retries |
| `pipeline_progress.json` not updating | Process died without cleanup, disk full |
| Circuit breaker OPEN in logs | 3+ consecutive LLM/ASR failures |

## Diagnosis

### Step 1: Check ExecutionTrace

```bash
# List recent traces
ls -lt output/logs/traces/

# Inspect a specific trace (replace TRACE_ID)
cat output/logs/traces/TRACE_ID.json
```

Look for:
- `DEAD_LETTER` status = permanent failure, not resumable
- `FAILED` status = may be resumable from last `COMPLETED` stage
- Hash chain break = input changed since last run

### Step 2: Check Circuit Breaker State

```bash
# Search logs for circuit breaker state changes
grep "Circuit \[" output/logs/noteforge.log | tail -20
```

If circuit is OPEN, wait for recovery timeout or restart pipeline.

### Step 3: Check Pipeline Logs

```bash
# Recent errors
grep -i "error\|failed\|exception" output/logs/noteforge.log | tail -30

# Last 50 lines of log
tail -50 output/logs/noteforge.log
```

### Step 4: Check Progress File

```bash
cat output/logs/pipeline_progress.json
```

## Recovery

### Option A: Doctor Check

```bash
envs\paraformer\python.exe -m noteforge --doctor
```

Runs health checks on all components (Python, ASR, LLM, Feishu, config).

### Option B: Resume from Checkpoint

```bash
# Resume auto pipeline from last checkpoint
envs\paraformer\python.exe -m noteforge.batch.auto_pipeline urls.txt --resume
```

### Option C: Resume Batch with Specific Checkpoint

```bash
# Resume batch processing, skip already-completed items
envs\paraformer\python.exe -m noteforge --batch --resume
```

### Option D: Clear Stuck Progress

```bash
# If progress file is corrupted or stuck
envs\paraformer\python.exe -m noteforge --progress --clear

# Or manually delete
del output\logs\pipeline_progress.json
```

### Option E: Single Item Retry

```bash
# Retry a single failed item
envs\paraformer\python.exe -m noteforge --input ep01 --force
```

## Rollback (5-minute procedure)

1. **Identify last good output**:
   ```bash
   ls -lt output/notes/ | head -10
   ```

2. **Remove corrupted output** (if any):
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
- ExecutionTrace shows `DEAD_LETTER` on 3+ items in a single run
- Circuit breaker stays OPEN after 10+ minutes
- `--doctor` reports ASR or LLM as unhealthy and local recovery fails
- Disk space under 1GB (can cause silent write failures)
- Repeated hash chain breaks suggest config drift or file corruption
