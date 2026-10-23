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

Use `scripts/run-benchmarks` for changes to runtime hot paths, including native code.
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

## Baselines and artifacts

- The default baseline is a published ddtrace package. Select an explicit baseline with `--baseline`
  when needed; state exactly what was compared. Do not attribute all differences from an older
  release to this patch. See `scripts/run-benchmarks --help` for accepted options.
- Keep build optimizations comparable. Do not enable `DD_FAST_BUILD` for performance comparisons
  against optimized release wheels.
- `--configs` filters every selected scenario; use separate invocations when config names differ.
- Save artifacts and record the run ID. Multiple `--scenario` flags share a run; `--run-id` can
  add scenarios to an existing artifact directory. Avoid confusing earlier runs with the latest one.

```bash
scripts/perf-analyze benchmark-artifacts/<run-id>/
scripts/perf-analyze benchmark-artifacts/<run-id>/ --json
```

Use `--profile` only when needed to explain results; its artifacts can be large. Analyze with
`--profile-compare` / `--profile-top`, narrowing configs and function filters as appropriate.

## Interpret results

Report baseline/candidate values, variation, absolute and relative differences, and limitations.
A fixed percentage does not determine statistical significance. Repeat noisy measurements under
comparable conditions; a small stable regression in a hot path can matter.

For CI gates, consult the scenario's actual SLO and ownership in `.gitlab/benchmarks/slos/`
and [.gitlab/benchmarks/microbenchmarks.md](../../../.gitlab/benchmarks/microbenchmarks.md).
Do not substitute a universal 2%/5% threshold or alter an SLO just to make a regression pass.
