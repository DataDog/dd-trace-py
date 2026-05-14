---
name: profile-local
description: >
  Run a profiling workload locally against a real Datadog agent to validate
  profiler changes end-to-end. Covers agent startup, ddtrace-run invocation,
  and verification in the Datadog UI. Use when testing profiling feature changes
  (heap tracking, MEM domain, etc.) with a trustworthy real-world signal.
allowed-tools:
  - Bash
  - Read
---

# Profile Local Skill

End-to-end profiling validation using a real Datadog agent, a stdlib-only workload,
and `ddtrace-run`. Use this instead of the self-contained bench scripts when you
want profiles that appear in the Datadog UI exactly as a user would see them.

## When to Use This Skill

- Validating a new profiling feature (e.g., MEM domain tracking) against real agent ingestion
- Confirming heap profiles show the expected frames before opening a PR
- Debugging profiler behavior that can't be reproduced in unit tests

---

## Step 1 — Load the API key

The local agent needs a real `DD_API_KEY` to forward profiles to Datadog:

```bash
# The repo keeps secrets in .env (gitignored). Source it:
source .env

# Verify it's set:
echo "DD_API_KEY=${DD_API_KEY:0:8}..."
```

If `.env` doesn't exist, export the key manually:
```bash
export DD_API_KEY=<your-key>
export DD_SITE=datadoghq.com   # or datadoghq.eu, us3.datadoghq.com, etc.
```

---

## Step 2 — Start the local Datadog agent

```bash
DD_API_KEY="$DD_API_KEY" DD_SITE="${DD_SITE:-datadoghq.com}" \
  docker compose up -d ddagent
```

Verify it's healthy (should return JSON with `{"status":"ok",...}`):
```bash
curl -s http://localhost:8126/info | python -m json.tool | head -20
```

If the agent is already running from a previous session, just verify:
```bash
curl -s http://localhost:8126/info | python -m json.tool | grep version
```

---

## Step 3 — Rebuild the C extension (if needed)

Required any time you switch branches or modify `.cpp`/`.h` files:

```bash
python setup.py build_ext --inplace -q
```

This takes ~60s on a first build; subsequent builds are faster due to incremental
compilation.

---

## Step 4 — Run the workload under ddtrace-run

Use `scripts/mem_domain_workload.py` — a stdlib-only infinite loop that allocates
~100 MB in each CPython memory domain and sleeps 60s between cycles. It knows
nothing about profiling.

**With MEM domain enabled (feature branch):**
```bash
DD_SERVICE=mem-domain-test \
DD_ENV=dev \
DD_VERSION=local \
DD_PROFILING_ENABLED=true \
DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=true \
DD_AGENT_HOST=localhost \
  .venv/bin/ddtrace-run python scripts/mem_domain_workload.py
```

**OBJ-only baseline (default, MEM domain off):**
```bash
DD_SERVICE=mem-domain-test \
DD_ENV=dev \
DD_VERSION=local \
DD_PROFILING_ENABLED=true \
DD_AGENT_HOST=localhost \
  .venv/bin/ddtrace-run python scripts/mem_domain_workload.py
```

The profiler uploads every 60s by default. Let the script run for at least 2 cycles
(~2 minutes) before checking the UI.

---

## Step 5 — Verify profiles appear

**Agent side** (confirm the agent received the profiles):
```bash
docker compose logs ddagent | grep -i profil | tail -20
```

**Datadog UI:**
1. Open [https://app.datadoghq.com/profiling](https://app.datadoghq.com/profiling)
   (or your `DD_SITE` equivalent)
2. Filter: **Service** = `mem-domain-test`, **Env** = `dev`
3. Open a profile → **Heap** tab
4. Expected with `DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=true`:
   - `_alloc_mem` visible at ~100 MB (array.array buffers + list ob_item)
   - `_alloc_obj` visible at ~100 MB (bytes objects)
5. Expected without (baseline):
   - `_alloc_mem` **absent or near-zero**
   - `_alloc_obj` visible at ~100 MB

---

## Per-version testing (staging)

The local flow above tests against whatever Python is in `.venv`. For
multi-version coverage on the real Rapid smoke-test service, use
`update_ddtrace_wheels.sh --python-version 3.X <pipeline-id>` from a dd-source
checkout (script lives on the `kowalski/experiment-profiling-integration-branch-scripts`
branch in dd-trace-py).

Each invocation pins the smoke-test deployment to one Python version. Profiles
land under `service:rapid_python_http_smoke_test_py3X` in the Datadog UI for
easy per-version filtering.

```bash
# In a dd-source checkout:
update_ddtrace_wheels.sh --python-version 3.11 <pipeline-id-or-commit-sha>
```

The script handles three knobs in one commit per version:
1. `requirements.in` — filtered to just that Python's wheel (no marker fan-out)
2. `rapid.json` — injects `DD_SERVICE=..._py3X` so the UI separates versions
3. `BUILD.bazel` — pins `python_version = "3.X"` on the Rapid target (this is the actual runtime knob, since the Bazel default is 3.12)

### What to look for per version

| Python | Watch for | Why |
|---|---|---|
| 3.9 | No crash; `_alloc_mem` ~100 MB | First real-service test of the Python-level GC fallback in `pygc_temp_disable_guard_t` |
| 3.10, 3.11 | `_alloc_mem` ~100 MB; Locked Time delta vs OBJ-only baseline manageable | `PyGC_Disable`/`Enable` C-API guard exercised; the original PROF-11496 symptom was lock-hold-time regression |
| 3.12 | `_alloc_mem` ~100 MB; no GC-guard cost | Baseline path — GC deferred during allocation, no guard needed |
| 3.13+ | Same as 3.12, **plus** `bytearray(...)` callsites visible | 3.13 moved `bytearray`'s buffer from `PyObject_Malloc` → `PyMem_Malloc`; without MEM hooks it's invisible. With them on, captured automatically |

A useful comparison: deploy `--python-version 3.13` twice — once with
`DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=false`, once with `=true` — and diff
the heap profile. `bytearray` should only appear in the second.

---

## Troubleshooting

### `ddtrace-run` not found
```bash
# Use the venv directly:
.venv/bin/ddtrace-run --version

# Or install ddtrace into your active env:
pip install ddtrace
```

### Agent not reachable (`curl` returns connection refused)
```bash
# Check if the container is running:
docker compose ps ddagent

# Check logs for startup errors:
docker compose logs ddagent | tail -30

# Restart:
DD_API_KEY="$DD_API_KEY" docker compose restart ddagent
```

### `DD_API_KEY` invalid / profiles not appearing in UI
- Confirm the key is for the right org and site (`DD_SITE`)
- Check agent logs: `docker compose logs ddagent | grep -i "api key"`
- Profiles may take 2–5 minutes to index after the first upload

### C extension not updated after branch switch
Symptoms: profiler behavior matches old branch, or you see an `ImportError`.
Fix: `python setup.py build_ext --inplace -q`

### Heap profile shows no `_alloc_mem` frame
- Confirm `DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=true` is set (not just exported — pass
  it explicitly on the same command line as `ddtrace-run`)
- Confirm the C extension was rebuilt on the feature branch (not `main`)
- Run `ddtrace-run --info` and check `mem_domain_enabled` in the profiling config output

---

## Cleanup

```bash
docker compose stop ddagent
```
