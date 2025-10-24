# RLock Profiling - Super Simple Quick Start

## TL;DR

```bash
# 1. Set your API key (one time)
export DD_API_KEY="your-datadog-api-key"
export DD_SITE="datadoghq.com"

# 2. Run a test (pick one)
python3 scripts/rlock_simple_demo.py              # 30 seconds
python3 scripts/rlock_heavy_contention.py         # 60 seconds, BEST!

# 3. View results (wait 30 seconds after script ends)
# https://app.datadoghq.com/profiling
# Filter by service: rlock-simple-demo or rlock-heavy-contention
```

That's it! 🎉

## What to Look For in UI

After running, go to https://app.datadoghq.com/profiling and look for:

1. **Lock-acquire samples** - where locks are acquired
2. **Lock-release samples** - how long locks were held
3. **Lock-wait samples** - contention/waiting time
4. **Reentrant patterns** - same thread acquiring lock multiple times

## Scripts Available

| Script | Time | What It Shows |
|--------|------|---------------|
| `rlock_simple_demo.py` | 30s | Basic RLock usage |
| `rlock_heavy_contention.py` | 60s | **EXTREME contention - best for demos!** |
| `rlock_stress_test.py` | 60-300s | Comprehensive production-like test |

## Why Agentless Mode?

These test scripts use **agentless mode** (direct to Datadog) because it's simpler:

- ✅ No agent setup needed
- ✅ Just 2 environment variables  
- ✅ Works immediately
- ✅ Perfect for testing and demos

For production apps, you'd typically use the agent instead (set `DD_AGENT_HOST=localhost`), but for these tests, agentless is easier!

## Troubleshooting

**No samples appearing?**

1. Wait 30-60 seconds after script completes
2. Check: `echo $DD_API_KEY` (should show your key)
3. Check: `echo $DD_SITE` (should show datadoghq.com or your site)
4. Try running with debug: `DD_TRACE_DEBUG=true python3 scripts/rlock_simple_demo.py`

**Which Datadog site do I use?**

- US: `datadoghq.com`
- EU: `datadoghq.eu`
- US3: `us3.datadoghq.com`
- US5: `us5.datadoghq.com`
- AP1: `ap1.datadoghq.com`

## Next Steps

Once you verify the simple test works:

```bash
# Run the dramatic heavy contention demo (best for showing off!)
python3 scripts/rlock_heavy_contention.py
```

This creates extreme lock contention with:
- 15 threads fighting for locks
- Very obvious wait times
- Deep reentrant lock patterns
- Perfect for demos and presentations! 🎬

---

**Full docs:** See `RLOCK_PROFILING_DEMO.md` for complete documentation

