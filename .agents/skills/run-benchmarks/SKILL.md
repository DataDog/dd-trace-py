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

# Benchmark runtime changes

Use `scripts/run-benchmarks` for changes to native runtime code and other performance-sensitive
paths, including startup/bootstrap.
Documentation, comments, and test-only edits do not need runtime benchmarks. If discovery has no
matching scenario for a runtime change, investigate coverage rather than concluding it is unnecessary.

```bash
scripts/run-benchmarks --list <changed-files>
scripts/run-benchmarks --list --all-suites
scripts/run-benchmarks --dry-run --scenario <scenario> --artifacts ./benchmark-artifacts/
scripts/run-benchmarks --scenario <scenario> --artifacts ./benchmark-artifacts/
```

The runner wraps `scripts/perf-run-scenario`; use the wrapper, not that script directly.
Docker is required. Discover current scenarios/configs from the runner and `benchmarks/suitespec.yml`.
Choose scenarios that exercise the changed behavior, starting with focused configs during iteration.
Run all configs of the selected scenarios for final results; report any coverage that could not run.

`--all-suites --list` includes registered suites only. Other scenarios, such as `encoder`, can
still run directly. Find their `scenario.py` and inspect the adjacent `config.yaml` first:

```bash
rg --files benchmarks -g scenario.py
scripts/run-benchmarks --dry-run --scenario encoder --configs one-trace --artifacts ./benchmark-artifacts/
```

Remove `--dry-run` to execute after checking the selection. Keep listing separate from execution:
`--scenario <name> --list` does not list that scenario's configs; read its `config.yaml` instead.

## Baselines and artifacts

- The default baseline is a published ddtrace package. Select an explicit baseline with `--baseline`
  when needed; state exactly what was compared. Do not attribute all differences from an older
  release to this patch. See `scripts/run-benchmarks --help` for accepted options.
- Keep build optimizations comparable. Do not enable `DD_FAST_BUILD` for performance comparisons
  against optimized release wheels.
- `--configs` filters every selected scenario; use separate invocations when config names differ.
  Unknown names are filtered out, potentially leaving no configs to run. Check the actual result
  files for every requested config and both baseline/candidate; a successful exit is not coverage.
- Save artifacts and record the run ID. Multiple `--scenario` flags share a run; `--run-id` can
  add scenarios to an existing artifact directory. Avoid confusing earlier runs with the latest one.
  Passing only the artifacts root to `perf-analyze` selects the most recently modified child
  directory. Prefer the recorded run ID, and use a new ID when code or comparison inputs change.

```bash
scripts/perf-analyze benchmark-artifacts/<run-id>/
scripts/perf-analyze benchmark-artifacts/<run-id>/ --json
```

Use `--profile` only when needed to explain results; its artifacts can be large. Analyze with
`--profile-compare` / `--profile-top`, narrowing configs and function filters as appropriate.

```bash
scripts/perf-analyze benchmark-artifacts/<run-id>/ --profile-compare --filter ddtrace --min-calls 1000
scripts/perf-analyze benchmark-artifacts/<run-id>/ --profile-compare --sort cumtime --save-pstats
python -m pstats benchmark-artifacts/<run-id>/<scenario>/baseline/viztracer/<config>.prof
```

`--save-pstats` writes `.prof` files beside the input profiles; exports reflect any filters you
apply. `--filter` matches function names/paths and `--min-calls` filters each side independently,
so either can hide relevant calls. Omit them when investigating rare calls or exporting full profiles.
Filtering also removes child events before exclusive-time calculation; confirm attribution with
unfiltered profiles when those children matter.
See `scripts/perf-analyze --help` for `--profile-top`, `--sort`, and `--markdown` output options.

Read the comparison columns according to the selected metric:

| Column | Meaning |
|--------|---------|
| `B calls` / `C calls` | Raw baseline/candidate call counts; pyperf can choose different iteration counts, so differences alone do not prove changed behavior. |
| `tottime/call` | Exclusive time per call, excluding child calls; the default comparison metric. |
| `cumtime/call` | Time per call including children, selected by `--sort cumtime`. |
| `Delta` / `%` | Candidate minus baseline for the displayed metric; positive timing deltas mean slower calls. Check absolute cost as well as percent. |
| `new` | The baseline metric is zero or missing, possibly because of filtering; verify before attributing it to new code. |

Prioritize expensive calls in frequently executed paths, while keeping workload/iteration counts
comparable. Venv paths and shifted source line numbers are normalized for cross-version comparisons.

## Interpret results

Report baseline/candidate values, variation, absolute and relative differences, and limitations.
A fixed percentage does not determine statistical significance. Repeat noisy measurements under
comparable conditions; a small stable regression in a hot path can matter.

For CI gates, consult the scenario's actual SLO and ownership in `.gitlab/benchmarks/slos/`
and [.gitlab/benchmarks/microbenchmarks.md](../../../.gitlab/benchmarks/microbenchmarks.md).
Use that scenario's threshold; do not alter an SLO just to make a regression pass.
