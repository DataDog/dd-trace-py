## Description

The `profiling_native` job builds C++ tests via CMake (`build_standalone.sh`) and runs them under sanitizers and valgrind — **32 jobs per trigger** (6 Python versions × sanitizer/memcpy/valgrind combos).

Previously it triggered on **any** file change under `ddtrace/profiling/`, `ddtrace/internal/datadog/profiling/`, `src/native/`, and `tests/profiling/`. This included Python, Rust, docs, and test files that have zero effect on the C++ build graph.

**Fix:** replace broad directory globs with extension-based patterns matching only files in the CMake build graph:

- **Included:** `*.cpp`, `*.cc`, `*.h`, `*.hpp`, `*.pyx`, `*.cmake`, `CMakeLists.txt`, `*.sh`, `*.supp`, `setup.py`, `pyproject.toml`
- **Excluded automatically:** `*.py`, `*.rs`, `*.md`, `*.pyi`, and everything else

New native files are covered automatically; new Python/Rust files are excluded automatically — no tribal knowledge about directory layout required.

### Measured impact (Jan 1 – Feb 12, 2026)

Actual durations fetched from the GitLab API for every `profiling_native` job across all 46 unnecessarily-triggered pipelines:

| Metric | Value |
|--------|-------|
| Commits that triggered `profiling_native` (old rules) | 141 / 381 |
| **Would now skip** | **37 of those had `profiling_native` jobs** |
| Total unnecessary jobs | **1,212** |
| **Total CI-time wasted** | **144.6 hours** |
| Average per job | 7m 10s |
| Average per trigger | ~235 min (32 jobs) |
| **Annualized** | **~1,254 CI-hours/year** |

Breakdown of the 37 triggered pipelines:
- **30 recent** (32-job matrix): 88 h total, avg 5m 30s/job, ~176 min/trigger
- **7 older** (36-job matrix): 57 h total, avg 13m 30s/job, ~486 min/trigger

Commit categories: 36 Python-only, 9 Rust-only (`src/native/`), 1 docs.

> Analysis scripts: [`scripts/ci-analysis/`](https://github.com/DataDog/dd-trace-py/tree/vlad/ci-analysis-scripts/scripts/ci-analysis) — reproducible with a `read_api` GitLab token.

Scheduled pipelines and `main`-branch pushes are unchanged — they always run `profiling_native`.

## Testing

1. **Live negative test** — [PR #16498](https://github.com/DataDog/dd-trace-py/pull/16498) demonstrates the skip on a real GitLab pipeline. The test branch has the narrowed rules, and a second push changed only `_lock.py` (a Python file). Since `rules:changes` evaluates the push diff (not the full branch diff), the only file in scope is `_lock.py`, which doesn't match any native extension pattern. Reviewers can verify `profiling_native` jobs are **absent** from the latest pipeline.

2. **Pattern audit** — cross-reference against `find ddtrace/internal/datadog/profiling -type f` and `find ddtrace/profiling -type f`:
   - 112 files in `ddtrace/internal/datadog/profiling/`: 100 matched, 12 correctly excluded (Python/Rust/docs), 0 missing
   - All `.pyx` files in `ddtrace/profiling/` matched

3. **Safety nets untouched** — `schedule` and `main` rules are not modified; full native test coverage continues on those pipelines.

## Risks

- **Under-triggering:** If a new file type enters the C++ build graph, its extension must be added. Mitigated by: (a) `CMakeLists.txt`, `.sh`, `.supp` already covered, (b) scheduled/main pipelines always run, (c) this is the first such change in 2+ years.
- **Self-trigger on this PR:** This PR modifies `.gitlab-ci.yml`, so `profiling_native` runs here. Expected — narrowed rules take effect after merge.

## Additional Notes

- `src/native/**/*` removed — Rust code is built via cargo/maturin, not CMake. `build_standalone.sh` does not compile or link it.
- `tests/profiling/**/*` removed — C++ tests live under `ddtrace/internal/datadog/profiling/*/test/` (already covered); `tests/profiling/` contains only Python tests.
