# Feishu Authentication Troubleshooting Runbook

## Symptoms

| Symptom | Likely Cause |
|---------|-------------|
| `lark-cli: command not found` | lark-cli not installed or not in PATH |
| `401 Unauthorized` | App access token expired or invalid |
| `403 Forbidden` | App lacks required permissions |
| `Space not found` | Wrong `space_id` or app not added to space |
| `Node not found` | Wrong `root_node_token` or node deleted |
| Sync partially succeeds (some docs fail) | Rate limiting or permission gaps |
| `token expired` in logs | Access token needs refresh |

## Diagnosis

### Step 1: Validate Feishu Integration

```bash
envs\paraformer\python.exe -m noteforge --feishu-validate
```

Checks: lark-cli availability, env vars (APP_ID/APP_SECRET/SPACE_ID/ROOT_NODE_TOKEN), API connectivity, root node access.

### Step 2: Check Environment Variables

```bash
echo %FEISHU_APP_ID%
echo %FEISHU_APP_SECRET%
echo %FEISHU_SPACE_ID%
echo %FEISHU_ROOT_NODE_TOKEN%
```

All four should be non-empty. If any is empty, check `.env`:

```bash
type ..\.env | findstr FEISHU
```

### Step 3: Check YAML Config (fallback source)

```bash
type config\llm_engine_config.yaml | findstr /i "feishu space root_node"
```

Empty YAML values fall back to env vars. YAML values override env vars when set.

### Step 4: Test lark-cli Manually

```bash
lark-cli --version
lark-cli auth status
```

## Recovery

### Fix 1: Re-authenticate (--feishu-auth)

```bash
envs\paraformer\python.exe -m noteforge --feishu-auth
```

Guided flow: checks lark-cli, verifies .env credentials, launches browser auth. After success, run `--feishu-validate` to confirm.

### Fix 2: Install lark-cli

```bash
npm install -g @anthropic-ai/lark-cli

# If no npm, install Node.js first
winget install OpenJS.NodeJS.LTS
```

### Fix 3: Fix Missing Credentials in .env

```bash
# Edit .env (project root, one level up from video-to-text)
notepad ..\.env
```

Required values:
```
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_SPACE_ID=xxxxxxxxxxxx
FEISHU_ROOT_NODE_TOKEN=xxxxxxxxxxxx
```

Get credentials: https://open.feishu.cn/app -> create app -> "Credentials & Basic Info" page.

### Fix 4: Fix space_id / root_node_token

```bash
# List accessible spaces
lark-cli space list

# Get root node token for a space
lark-cli space info --space-id %FEISHU_SPACE_ID%
```

Update `.env` or `llm_engine_config.yaml` with correct values.

### Fix 5: Dry-Run Sync to Verify

```bash
envs\paraformer\python.exe -m noteforge.integration.feishu_sync --dry-run
```

### Fix 6: Clear Sync Cache (stale/corrupted)

```bash
del output\logs\feishu_sync_cache.json
envs\paraformer\python.exe -m noteforge.integration.feishu_sync --new-only
```

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `lark-cli: command not found` | Not installed | `npm install -g @anthropic-ai/lark-cli` |
| `401 Unauthorized` | Token expired | Re-run `--feishu-auth` |
| `403 Forbidden` | App missing permissions | Add app to space in Feishu admin; enable `wiki:wiki` scope |
| `Space not found` | Wrong space_id | Check `lark-cli space list`, update `.env` or YAML |
| `Node not found` | root_node_token invalid | Check space structure, update token |
| `429 Too Many Requests` | Rate limited | Wait 60s, retry with `--new-only` |
| `ConnectionError` | Network/proxy issue | Check proxy settings, try direct connection |
| `category match failed` | No matching category rule | Add match rule in `llm_engine_config.yaml` categories |
| Partial sync failure | Unsupported blocks in some docs | Check logs for specific block errors |
| `parent_node_token invalid` | Category node deleted | Re-create category structure in Feishu |
| Auth timeout (120s) | Browser auth window not completed | Re-run `--feishu-auth`, complete browser prompt promptly |
