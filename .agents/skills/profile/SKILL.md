---
name: profile
description: >
  Profile dd-trace-py code with Austin (>= 4.1) to find where time or memory
  goes and spot optimization opportunities. A workload script that exercises
  the code is run under Austin in the testrunner container, and the profile is
  analyzed on the host with the Austin VS Code MCP tools, the austin-query CLI,
  or a Python script over the austin.query API. Use when the user asks to
  profile, or offer it when a change warrants a thorough performance analysis:
  hot paths (span creation and finishing, context propagation, integration
  wrappers, encoders, writers), startup/bootstrap, periodic threads, locks,
  memory growth, or when a benchmark regression needs explaining.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
---

# Profile with Austin

Austin is a sampling profiler for CPython. It needs no instrumentation and
barely affects the target. The workflow is: write a script that exercises the
code under study, run it under Austin in the testrunner container, then
investigate the profile on the host until it explains where the cost is and
what could be done about it.

## When to use

If a change touches code that runs per request or per span, startup, or a
path where a benchmark moved and the reason is unclear, offer a profiling pass
in one line rather than running it unasked. If the user asks for it, go ahead.
Benchmarks (`run-benchmarks`) say *whether* performance changed. A profile
says *where* the cost is and *why*.

## 1. Prepare the environment

Pick an environment that has what the workload imports, and make sure it is
current. `scripts/run-tests` has no build-only mode, so give it a selection
that matches no tests:

```bash
scripts/run-tests --list <changed-files>
scripts/run-tests --venv <hash> -- -k __build_only__
```

Add `-r` after changing native sources. The environment is an editable install
of the checkout, so Python changes are live without rebuilding.

Native extensions in these environments are built with `DD_FAST_BUILD=1`
(set by `docker-compose.base.yml`), which compiles some of them at `-O0`.
Python-level costs are representative; time inside native code is not. If
native timings matter, say so, and ask the user how they want an optimized
build produced before drawing conclusions.

## 2. Write the workload

The workload decides what the profile can tell you. Put it under
`profile-artifacts/` (gitignored, and visible in the container as
`/home/bits/project/profile-artifacts/`). Host paths outside the checkout,
like `/tmp`, are not visible in the container.

- Exercise the code under study in a loop that runs for a few seconds, so it
  gets thousands of samples at `-i 1ms`. Keep unrelated work out of the loop.
- Start with `import ddtrace.auto` on line 1 when the scenario involves
  auto-instrumentation, the only supported setup.
- Read knobs from environment variables (payload size, number of tags,
  integration on/off) rather than writing one script per variant.
- The container has no agent. The writer fails to send (logged as dropped
  traces), and `Tracer.shutdown` can wait seconds at exit. That is fine as
  long as the analysis isolates the workload (step 4). If writer or flush
  behavior is what you are studying, point `DD_TRACE_AGENT_URL` at a reachable
  agent instead.

```python
import ddtrace.auto  # noqa: F401

import os

from ddtrace.trace import tracer


EXTRA_TAGS = int(os.environ.get("EXTRA_TAGS", "0"))

for _ in range(100_000):
    with tracer.trace("root", service="svc") as root:
        for i in range(EXTRA_TAGS):
            root.set_tag(f"extra{i}", i)
        with tracer.trace("child"):
            pass
```

## 3. Run Austin in the container

```bash
.agents/skills/profile/austin-docker --venv <hash> --name <name> [--env KEY=VALUE]... [AUSTIN_OPTION]... -- python profile-artifacts/workload.py
```

The helper installs `austin-dist>=4.1` from PyPI into a throwaway venv in the
container, puts the test environment on `PATH`, and writes
`profile-artifacts/<name>.mojo` (or `<name>-1.mojo` ... with `--runs N`).
Use a new name for each experiment.

| Option | Use |
|--------|-----|
| `-i 1ms` | Sampling interval; 1ms keeps files small, the default is 100us |
| `-c` | CPU time instead of wall time |
| `-f` | Full metrics: wall time, CPU flag and memory deltas in one run |
| `-m` | Memory deltas (RSS between samples), for allocation-heavy code |
| `-g` | Mark samples taken while the GC was collecting |
| `-n` | Native stacks interleaved with Python frames; use `-i 1ms` or more, it suspends threads |
| `-C` | Follow child processes (pytest-xdist, multiprocessing, gunicorn workers) |
| `-x 30` | Stop after N seconds |

asyncio task data is collected automatically on Python 3.14+, except in native
mode. To profile a test, run pytest under Austin inside the helper
(`-- python -m pytest -p no:randomly tests/...`), and drop `-n auto` or add `-C`.

Austin needs `CAP_SYS_PTRACE`. The testrunner service already has it; any
other container needs `--cap-add SYS_PTRACE`.

## 4. Investigate

### Isolate the workload first

A wall-time profile covers every thread for the whole process lifetime:
imports and bootstrap, the workload, shutdown, and ddtrace's background
threads (writer, telemetry, periodic services), which spend most of their
time idle. A hot-spot list over all of it is misleading. In a profile of the
span loop above, the loop was only 13% of the main thread: 66% was
`Tracer._atexit` waiting to flush to the missing agent, and 11% was imports.
Before reading hot spots:

1. Look at the summary (threads and their totals) and a coarse tree
   (`tree(min_pct=5, max_depth=6)`) to see the phases.
2. Narrow the samples to the phase you care about with a frame that only
   that phase has on the stack (`contains="workload.py:<module>"`), plus the
   workload thread. Idle filtering (`idle=False`, `-c`) helps with background
   threads, but it does not catch every wait: the shutdown flush above is
   reported as non-idle, so do not rely on it to isolate a phase.
3. Look at background threads separately when they are the subject, e.g. the
   writer's flush cost.

### Look for optimization opportunities

- `top` by own time: code that is expensive in itself (tight loops,
  allocations, repeated lookups). `<self>` in `callees` is the same thing for a
  single function.
- `top` by total time and `tree`: expensive paths. Follow the heaviest chain
  down until the cost spreads out.
- `callers`: whether a hot function is called from a path that could avoid it
  (caching, hoisting out of the loop, skipping when disabled).
- `lines`: which statements inside a hot function carry the cost.
- `-m`/`-f` runs: allocation churn per operation; `-g` runs: GC pressure.

Link each finding to the source, read the code, and propose concrete changes
with the share of the workload they could save. Check expectations against the
profile: if something you expected to be cheap is not, that is a finding too.

### Choose an analysis tool

**VS Code MCP tools** (`mcp__austin__*`), when available and the user is in
the loop. They drive the flame graph the user sees.

1. `load_profile` with the absolute host path, then `get_metadata` to confirm.
2. `get_top`, then `get_call_stacks` with a `threshold` (e.g. 0.5) to prune.
3. `get_gc_data` (needs `-g`), `get_task_traces` (asyncio, 3.14+).
4. `search_flamegraph` / `focus_flamegraph` (with a `nodeId`) to show findings.

The MCP tools cannot filter by phase or thread, so use them to present
findings and do the narrowing with the query API. If the tools are missing,
the user can enable them with **Austin: Generate .mcp.json** in VS Code.

**austin.query** from `austin-python>=2.5`. Always install it in a throwaway
venv, never a global Python:

```bash
python3 -m venv /tmp/austin-query-venv
/tmp/austin-query-venv/bin/pip install -q 'austin-python>=2.5'
```

The `austin-query` CLI is fine for a quick single question:

```bash
Q="/tmp/austin-query-venv/bin/austin-query profile-artifacts/<name>.mojo"
$Q summary
$Q top -n 20 --thread MainThread --no-idle --contains 'workload.py:<module>'
$Q callers Span.set_tag
```

Each call reloads the profile, and the output is text tables (or `--json`).
For an investigation, prefer a script over the Python API: the profile loads
once, filtered views can be reused and composed, `where=` takes any predicate
over samples, results are typed objects you can aggregate (e.g. by ddtrace
subpackage), and the output can be joined with other data such as
`git diff --name-only`. Run it with `/tmp/austin-query-venv/bin/python -I`.

```python
from austin.query import ProfileQuery

q = ProfileQuery.from_file("profile-artifacts/<name>.mojo")
print(q.summary())
print(q.tree(min_pct=5, max_depth=6))

loop = q.filter(thread="MainThread", idle=False, contains="workload.py:<module>")
print(loop.top(15))                       # own time
print(loop.top(15, by="total"))           # paths
print(loop.callees("Tracer.start_span"))  # where its time goes
print(loop.lines("Span.set_tag"))         # which statements

# Typed results compose with plain Python, e.g. cost per ddtrace subpackage.
by_package = {}
for e in loop.top(None):
    path = e.location.filename
    if "/ddtrace/" in path:
        package = path.split("/ddtrace/", 1)[1].rsplit("/", 1)[0]
        by_package[package] = by_package.get(package, 0) + e.own_pct
```

Selectors are `function`, `file:function`, `file:function:line` (file matches
a path suffix), `re:<pattern>`, or a predicate over frames. `find(selector)`
shows what a selector matches. Results render as text when printed and convert
with `to_dict()`. Filters: `pid`, `thread`, `iid`, `idle`, `gc`, `contains`,
`task`, `where`. Python 3.14+ profiles also support `tasks()` and
`task_stacks(task)`. The source in `austin/query.py` of the installed package
has the full API.

### Container paths

Frames carry container paths (`/home/bits/project/...`). The query API
shortens them relative to the recorded cwd and venv, and selectors match
path suffixes, so they work unchanged. The VS Code extension cannot open those
sources. For click-through navigation, convert to collapsed stacks and rewrite
the prefix. This drops MOJO-only details such as column positions, so keep the
`.mojo` for analysis:

```bash
/tmp/austin-query-venv/bin/mojo2austin profile-artifacts/<name>.mojo /dev/stdout \
  | sed "s|/home/bits/project/|$PWD/|g" > profile-artifacts/<name>.austin
```

## Optional: compare before and after

When the user wants to know whether a change moved the profile, suggest a
comparison; it is an addition to the investigation, not a replacement. One
profile per side cannot separate a change from sampling noise, so collect
several runs of the same workload per side, then use `compare.py`, which
projects each run onto its heaviest functions and applies the two-sample
Hotelling T^2 test from [pyrometry](https://github.com/P403n1x87/pyrometry):

```bash
.agents/skills/profile/austin-docker --venv <hash> --name base --runs 8 -i 1ms -- python profile-artifacts/workload.py
# apply the change (or flip a workload knob with --env)
.agents/skills/profile/austin-docker --venv <hash> --name cand --runs 8 -i 1ms -- python profile-artifacts/workload.py
/tmp/austin-query-venv/bin/pip install -q 'pyrometry>=0.0.3'
/tmp/austin-query-venv/bin/python -I .agents/skills/profile/compare.py \
  --baseline profile-artifacts/base-*.mojo --candidate profile-artifacts/cand-*.mojo \
  --focus "workload.py:<module>" --thread MainThread
```

It reports total time and the share with ddtrace on the stack (Student's
t-test, the one-value case of the same test), the overall test result, and the functions that significantly
appeared, disappeared, grew or shrank. The test compares fewer functions than
there are runs (`-k`, capped at runs - 2), so more runs allow a wider view.
The per-function flags are simultaneous intervals and therefore conservative:
a large unflagged delta means the runs could not show it, not that nothing
changed. Collect more runs, or narrow `--focus`, before dismissing it.
Values are absolute, so both sides must run the same script doing the same
amount of work. Use `run-benchmarks` to put a number on the end-to-end effect.

## Reporting

State the environment hash, Python version, Austin options, workload and its
knobs, and the sample count of the phase analyzed. Report costs as a share of
that phase with the call path that leads to them, name the opportunities with
an estimate of what each could save, and flag numbers that come from `-O0`
native code.
