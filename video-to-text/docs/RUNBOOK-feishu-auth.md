# RUNBOOK: Feishu Authentication Troubleshooting

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

Checks lark-cli availability, auth status, and space access.

### Step 2: Check lark-cli

```bash
# Verify lark-cli is installed
lark-cli --version

# If not found, install globally
npm install -g @anthropic-ai/lark-cli
```

### Step 3: Check Environment Variables

```bash
# Verify required env vars are set
echo %FEISHU_APP_ID%
echo %FEISHU_APP_SECRET%
echo %FEISHU_SPACE_ID%
echo %FEISHU_ROOT_NODE_TOKEN%
```

All four should be non-empty. If any is empty, check `.env` file:

```bash
# .env file location (project root)
type ..\.env | findstr FEISHU
```

### Step 4: Check YAML Config

```bash
# Check feishu section in config
type config\llm_engine_config.yaml | findstr -i "feishu space root_node"
```

YAML values override env vars when set. Empty YAML values fall back to env vars.

### Step 5: Test Auth Manually

```bash
# Test lark-cli authentication
lark-cli auth status

# If auth fails, re-authenticate
lark-cli auth login --app-id %FEISHU_APP_ID% --app-secret %FEISHU_APP_SECRET%
```

## Recovery

### Fix 1: Re-authenticate

```bash
envs\paraformer\python.exe -m noteforge --feishu-auth
```

Or manually:

```bash
lark-cli auth login --app-id %FEISHU_APP_ID% --app-secret %FEISHU_APP_SECRET%
```

### Fix 2: Fix space_id / root_node_token

```bash
# List accessible spaces
lark-cli space list

# Get root node token for a space
lark-cli space info --space-id %FEISHU_SPACE_ID%
```

Update `.env` or `llm_engine_config.yaml` with correct values.

### Fix 3: Dry-Run Sync to Verify

```bash
# Dry-run shows what would sync without actually syncing
envs\paraformer\python.exe -m noteforge.integration.feishu_sync --dry-run
```

### Fix 4: Sync Only New Notes

```bash
# Skip notes already synced (by hash cache)
envs\paraformer\python.exe -m noteforge.integration.feishu_sync --new-only
```

### Fix 5: Clear Sync Cache

```bash
# If sync cache is stale or corrupted
del output\logs\feishu_sync_cache.json

# Re-run sync
envs\paraformer\python.exe -m noteforge.integration.feishu_sync
```

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `lark-cli: command not found` | Not installed | `npm install -g @anthropic-ai/lark-cli` |
| `401 Unauthorized` | Token expired | Re-run `--feishu-auth` |
| `403 Forbidden` | App missing permissions | Add app to space in Feishu admin |
| `Space not found` | Wrong space_id | Check `lark-cli space list`, update config |
| `Node not found` | root_node_token invalid | Check space structure, update token |
| `429 Too Many Requests` | Rate limited | Wait 60s, retry with `--new-only` |
| `ConnectionError` | Network/proxy issue | Check proxy settings, try direct connection |
| `category match failed` | No matching category rule | Add match rule in `llm_engine_config.yaml` |
| Partial sync failure | Some docs have unsupported blocks | Check logs for specific block errors |
| `parent_node_token invalid` | Category node deleted | Re-create category structure in Feishu |
