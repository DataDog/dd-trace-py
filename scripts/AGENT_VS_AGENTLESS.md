# Agent vs Agentless Mode for RLock Profiling

## The Issue You Encountered

You correctly noted that `DD_API_KEY` is already configured in your `datadog.yaml` file, but the scripts were asking for it as an environment variable. This is because there are **two different modes** for sending profiling data to Datadog.

## Two Modes Explained

### 🏠 Agent Mode (Recommended & Default)

**How it works:**
```
Your App → Local Datadog Agent → Datadog Backend
```

**Configuration:**
- API key stored in `/etc/datadog-agent/datadog.yaml`
- App sends to `localhost:8126` (agent)
- **No `DD_API_KEY` environment variable needed**
- Agent batches and forwards data

**Benefits:**
- ✅ Single API key configuration (in datadog.yaml)
- ✅ Better performance (local network only)
- ✅ Agent handles retries and buffering
- ✅ Works for all Datadog features (APM, profiling, logs, etc.)
- ✅ Recommended for production

**The scripts now default to this mode!**

### ☁️ Agentless Mode (Direct)

**How it works:**
```
Your App → Datadog Backend (directly)
```

**Configuration:**
- `DD_API_KEY` must be set as environment variable
- `DD_SITE` must be set (e.g., `datadoghq.com`)
- App sends directly to Datadog intake
- No local agent needed

**Benefits:**
- ✅ Simple for testing/demos (no agent required)
- ✅ Good for serverless/containerized environments
- ❌ Less efficient (each app connects directly)
- ❌ More environment variables to manage

## What Changed in the Scripts

### Before (Agentless):
```python
os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_SERVICE"] = "rlock-simple-demo"
# No DD_AGENT_HOST = sends directly to Datadog (needs DD_API_KEY)
```

### After (Agent Mode - Default):
```python
os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_SERVICE"] = "rlock-simple-demo"
os.environ["DD_AGENT_HOST"] = "localhost"  # ✅ Sends to agent
# No DD_API_KEY needed!
```

## How to Use Each Mode

### Using Agent Mode (Default)

Just run the scripts - they'll automatically use your local agent:

```bash
python3 scripts/rlock_simple_demo.py
```

The script will:
1. Connect to agent at `localhost:8126`
2. Agent reads API key from `datadog.yaml`
3. Agent forwards profiling data to Datadog

**Verify it's working:**
```bash
# 1. Check agent is running
datadog-agent status

# 2. Run script
python3 scripts/rlock_simple_demo.py

# 3. Check agent received data
tail -f /var/log/datadog/agent.log | grep profil
```

### Using Agentless Mode

If you want to bypass the agent (not recommended for production):

```bash
export DD_API_KEY="your-api-key-here"
export DD_SITE="datadoghq.com"
unset DD_AGENT_HOST  # Important: disable agent mode

python3 scripts/rlock_simple_demo.py
```

## Debugging: Which Mode Am I Using?

### Check with debug output:

```bash
DD_TRACE_DEBUG=true python3 scripts/rlock_simple_demo.py 2>&1 | grep -i "agent\|intake"
```

**Agent mode output:**
```
Connecting to agent at localhost:8126
Using agent for profiling data
```

**Agentless mode output:**
```
Connecting to intake at intake.profile.datadoghq.com
Using agentless profiling
```

### Check environment:

```bash
# Agent mode should show:
echo $DD_AGENT_HOST  # Should be "localhost"
echo $DD_API_KEY     # Should be empty (not needed)

# Agentless mode should show:
echo $DD_AGENT_HOST  # Should be empty
echo $DD_API_KEY     # Should have your key
echo $DD_SITE        # Should have your site (datadoghq.com, etc.)
```

## Why Your Agent Wasn't Receiving Samples

Before the fix, the scripts were in **agentless mode** but you hadn't set `DD_API_KEY`, so:
- Scripts tried to send directly to Datadog (agentless)
- No API key in environment → connection failed
- Agent was running and configured, but scripts weren't using it

Now the scripts are in **agent mode**, so:
- Scripts send to local agent at `localhost:8126`
- Agent uses API key from `datadog.yaml`
- Everything works! ✅

## Production Recommendation

**Use agent mode** (the new default) because:
- ✅ Single source of truth for API key (`datadog.yaml`)
- ✅ Better performance and reliability
- ✅ Consistent with other Datadog features (APM, logs)
- ✅ Agent handles buffering and retries
- ✅ Easier to manage in production

## Quick Reference

| Feature | Agent Mode | Agentless Mode |
|---------|-----------|---------------|
| **API Key Location** | `datadog.yaml` | `DD_API_KEY` env var |
| **Destination** | `localhost:8126` | Datadog intake |
| **Agent Required** | Yes | No |
| **Performance** | Better | Slower |
| **Production Use** | ✅ Recommended | ⚠️ Use with caution |
| **Setup Complexity** | Medium (agent setup) | Low (just env vars) |
| **Default in Scripts** | ✅ Yes | No |

---

## Summary

**Your setup is correct!** The issue was that the scripts weren't using your agent. Now they do, and you don't need to set `DD_API_KEY` as an environment variable. Your agent's `datadog.yaml` configuration is sufficient. 🎉

