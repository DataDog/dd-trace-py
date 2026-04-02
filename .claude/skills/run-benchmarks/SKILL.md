---
name: run-benchmarks
description: >
  Run performance benchmarks to measure the impact of code changes. Discovers
  relevant benchmark scenarios based on changed files, executes them comparing
  a baseline version against local changes, and summarizes performance results.
  Use this when touching performance-sensitive code paths or when asked about
  performance impact.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
---

# Benchmark Skill

This skill measures the performance impact of code changes by running the appropriate benchmark scenarios. It uses `scripts/run-benchmarks` to discover relevant scenarios from changed files and wraps `scripts/perf-run-scenario` for execution. Use `scripts/perf-analyze` to analyze saved artifacts after a run.

## When to Use This Skill

Use this skill when:
- Modifying performance-sensitive code (span creation, tracing hot paths, encoding, propagation)
- Changing C/Cython/Rust code (native extensions, `src/native/`, `.pyx`, `.rs` files)
- A reviewer asks "what's the performance impact?"
- Optimizing or refactoring code that runs in production hot paths
- Changes touch `ddtrace/_trace/`, `ddtrace/internal/encoding*`, `ddtrace/internal/writer/`, or framework integrations

## Key Principles

1. **Always use `scripts/run-benchmarks`** — never invoke `scripts/perf-run-scenario` directly
2. **Always save artifacts** — pass `--artifacts ./benchmark-artifacts/` so results persist for analysis
3. **Use `--list` first** — discover matching suites before running anything
4. **Use `--dry-run`** — verify the command before committing to a long Docker build
5. **Start with 1-2 scenarios** — benchmarks take minutes; target the most relevant ones
6. **Use `--configs` to filter** — run 1-2 configs during iteration; run all configs for final results
7. **Benchmarks require Docker** — confirm Docker is running before executing
8. **Artifacts auto-resolve to latest run** — `scripts/perf-analyze artifacts/` always analyzes the most recent run

## How This Skill Works

### Step 1: Identify Changed Files

```bash
git status
```

### Step 2: Discover Matching Benchmark Suites

```bash
scripts/run-benchmarks --list ddtrace/_trace/span.py ddtrace/internal/encoding.py
```

This outputs JSON showing which scenarios match, their config variants, and CPUs per run.

For all suites:
```bash
scripts/run-benchmarks --list --all-suites
```

### Step 3: Select Scenarios to Run

#### Core Tracing Changes
When modifying `ddtrace/_trace/*`, `ddtrace/trace/*`, `ddtrace/internal/sampling.py`:
- **Primary**: `span` — measures span creation/finishing overhead
- **Secondary**: `tracer` — measures tracer-level operations

#### Encoding / Writer Changes
`ddtrace/internal/encoding*`, `ddtrace/internal/writer/*`:
- **Primary**: `encoder` — directly measures encoding throughput

#### HTTP Propagation Changes
`ddtrace/propagation/*`:
- **Primary**: `http_propagation_extract`, `http_propagation_inject`

#### Flask Integration Changes
`ddtrace/contrib/internal/flask/*`:
- **Primary**: `flask_simple`

#### Django Integration Changes
`ddtrace/contrib/internal/django/*`:
- **Primary**: `django_simple`

#### IAST / AppSec Changes
`ddtrace/appsec/iast/*`, `ddtrace/appsec/*`:
- **Primary**: The specific `appsec_iast_*` scenario matching your change

#### OTel Changes
`ddtrace/opentelemetry/*`:
- **Primary**: `otel_span`, `otel_sdk_span`

#### Native Code (C/Rust/Cython)
`src/native/*.rs`, `ddtrace/internal/native/*`:
- **Primary**: `rand` (for `rand.rs`), `span`/`tracer` for general span changes

#### Startup / Bootstrap Changes
`ddtrace/bootstrap/*`, `ddtrace/auto.py`:
- **Primary**: `startup`

### Step 4: Execute Selected Scenarios

**Always save artifacts** so you can re-analyze without re-running:

```bash
# Dry-run first
scripts/run-benchmarks --dry-run --scenario span --artifacts ./benchmark-artifacts/

# Run the benchmark (latest PyPI vs local)
scripts/run-benchmarks --scenario span --artifacts ./benchmark-artifacts/
```

**Iterate faster with specific configs:**
```bash
# Only 2 of 13 configs
scripts/run-benchmarks --scenario span --configs start,start-finish --artifacts ./benchmark-artifacts/
```

**Collect profiling data** (when you need to understand *why* results differ):
```bash
scripts/run-benchmarks --scenario span --configs start-finish --profile --artifacts ./benchmark-artifacts/
```
Note: `--profile` uses viztracer and generates ~700MB files per config variant. Use with `--configs` to limit scope.

**Specify an explicit baseline version:**
```bash
scripts/run-benchmarks --scenario span --baseline ddtrace==2.8.4 --artifacts ./benchmark-artifacts/
```

### Step 5: Analyze Results

**Quick summary (latest run):**
```bash
scripts/perf-analyze benchmark-artifacts/
```

**Analyze a specific run ID:**
```bash
scripts/perf-analyze benchmark-artifacts/<run-id>/
```

**JSON output for programmatic use:**
```bash
scripts/perf-analyze benchmark-artifacts/ --json
```

**Viztracer: top functions by time spent (ddtrace hot paths only):**
```bash
scripts/perf-analyze benchmark-artifacts/ --profile-top 20 --filter ddtrace --min-calls 1000
```

**Viztracer: diff between baseline and candidate** (shows regressions and improvements):
```bash
scripts/perf-analyze benchmark-artifacts/ --profile-compare --filter ddtrace --min-calls 1000
```

**Sort by cumtime instead of tottime** (cumtime = inclusive of subcalls):
```bash
scripts/perf-analyze benchmark-artifacts/ --profile-compare --sort cumtime
```

**Save .prof files for interactive pstats analysis:**
```bash
scripts/perf-analyze benchmark-artifacts/ --profile-compare --save-pstats
python -m pstats benchmark-artifacts/<run-id>/span/baseline/viztracer/start-finish.prof
```

### Step 6: Interpret Results

The summary table shows:

```
Scenario: span
======================================================================

  start-finish:
    baseline:  2.72 ms +/- 28.28 us
    candidate: 3.03 ms +/- 54.18 us
    change:    +11.21% (1.11x slower) [11.2% slower]
```

**Interpreting changes:**
- **< 2% difference**: Not significant — within measurement noise
- **2–5% change**: Small — mention it; generally acceptable for non-hot-path changes
- **5–10% improvement**: Noteworthy — highlight in PR description
- **> 10% improvement**: Significant win — definitely highlight
- **Any regression > 2%**: Investigate before merging
- **> 5% regression**: Blocking — must be addressed or justified

**Statistical note**: The ±stddev tells you about measurement stability. If the candidate's stddev overlaps with the baseline's mean, the result may not be reproducible. Wide stddev on the candidate but not the baseline can indicate the change introduced a new overhead with high variance (e.g., dict lookups that vary by key hash, GC pressure, lock contention).

**Profiling analysis**: The `--profile-compare` output shows which functions changed in total time between baseline and candidate. Look for ddtrace internal functions in the regression list — those point directly to what got slower.

## Examples

### Example 1: Optimizing Span Creation

```bash
scripts/run-benchmarks --list ddtrace/_trace/span.py
# Shows: span, tracer, core_api, ...

scripts/run-benchmarks --dry-run --scenario span --artifacts ./benchmark-artifacts/

# Quick iteration (2 configs)
scripts/run-benchmarks --scenario span --configs start,start-finish --artifacts ./benchmark-artifacts/
scripts/perf-analyze benchmark-artifacts/ --detail

# Once satisfied, full run for PR
scripts/run-benchmarks --scenario span --artifacts ./benchmark-artifacts/
scripts/perf-analyze benchmark-artifacts/ --json
```

### Example 2: Investigating a Regression with Profiling

When the summary shows a regression and you need to understand why:

```bash
# First get the numbers
scripts/run-benchmarks --scenario span --configs start-finish --artifacts ./benchmark-artifacts/
scripts/perf-analyze benchmark-artifacts/
# -> shows +11% regression

# Collect profiling to see where time went
scripts/run-benchmarks --scenario span --configs start-finish --profile --artifacts ./benchmark-artifacts/
scripts/perf-analyze benchmark-artifacts/ --profile-compare
# -> shows which functions regressed
```

### Example 3: Flask Integration Change

```bash
scripts/run-benchmarks --list ddtrace/contrib/internal/flask/patch.py
# Shows: flask_simple, flask_sqli, fork_time, startup

scripts/run-benchmarks --scenario flask_simple --configs tracer,baseline --artifacts ./benchmark-artifacts/
scripts/perf-analyze benchmark-artifacts/
```

## Troubleshooting

### Docker not running
Start Docker Desktop and retry.

### Build takes a long time
The first run builds a Docker image including the full ddtrace wheel (Rust/Cython compilation). This is expected and necessary for accurate comparison against PyPI wheels (both are compiled with full optimizations). Subsequent runs reuse Docker layer cache if the source hasn't changed significantly.

Note: `DD_FAST_BUILD` is intentionally **not set** when running benchmarks, because fast builds use `-O0` / disabled LTO which would make local builds slower than release wheels, skewing results.

### Results are noisy
Microbenchmarks on a developer laptop can be noisy due to background processes, thermal throttling, etc. For more reliable results:
- Close other applications
- Run multiple times and compare
- CI benchmarks run with CPU affinity on dedicated hardware for more stable results

### Scenario not in suitespec
Some scenarios exist in `benchmarks/` but aren't in `suitespec.yml` (e.g., `encoder`, `threading`). Run them directly with `--scenario`:
```bash
scripts/run-benchmarks --scenario encoder --artifacts ./benchmark-artifacts/
```

### Viztracer files are huge
Viztracer generates ~700MB files per config per version. Use `--configs` to limit to the specific config that shows the regression:
```bash
scripts/run-benchmarks --scenario span --configs start-finish --profile --artifacts ./benchmark-artifacts/
```

### Profile comparison shows venv paths as different functions
This is automatically handled — `scripts/perf-analyze` normalizes `.venv_ddtrace_v1` and `.venv_ddtrace_v2` to a canonical path before comparison.

## Technical Details

### How suite discovery works
`tests/suitespec.py` collects suitespecs from both `tests/` and `benchmarks/` directories. Benchmark suites are namespaced as `benchmarks::span`, etc. `scripts/run-benchmarks` filters to `benchmarks::*` suites and strips the prefix to get the scenario name.

### Artifacts structure
```
benchmark-artifacts/
  <run-id>/                    # UUID generated per docker run
    <scenario>/
      baseline/
        results.<config>.json  # pyperf result JSON
        viztracer/             # only if --profile
          <config>.json        # Chrome Trace format (~700MB)
      candidate/
        results.<config>.json
        viztracer/
          <config>.json
```

`scripts/perf-analyze artifacts/` always picks the most recently modified run-id. Pass `artifacts/<run-id>/` to target a specific run.

### Why no DD_FAST_BUILD
`DD_FAST_BUILD=1` compiles with `-O0`, disables Rust LTO, reduces opt-level from 3 to 2, and skips Abseil — making builds faster but ~20–50% slower at runtime. Since benchmarks compare local builds against PyPI release wheels (compiled with full optimizations), using `DD_FAST_BUILD` would make the candidate artificially slower and skew results.

### Available scenarios

Use `scripts/run-benchmarks --list --all-suites` for the canonical up-to-date list. As a quick reference snapshot (may go stale):

Tracked in suitespec: `span`, `tracer`, `core_api`, `set_http_meta`, `telemetry_add_metric`, `otel_span`, `otel_sdk_span`, `recursive_computation`, `sampling_rule_matches`, `http_propagation_extract`, `http_propagation_inject`, `rate_limiter`, `appsec_iast_aspects`, `appsec_iast_aspects_ospath`, `appsec_iast_aspects_re_module`, `appsec_iast_aspects_split`, `appsec_iast_propagation`, `packages_package_for_root_module_mapping`, `packages_update_imported_dependencies`, `fork_time`, `django_simple`, `flask_simple`, `flask_sqli`, `errortracking_django_simple`, `errortracking_flask_sqli`, `startup`, `code_provenance`, `rand`

Untracked (use `--scenario` directly): `encoder`, `threading`, `coverage_fibonacci`, `events_api`, `iast_ast_patching`, `appsec_iast_django_startup`
