# Python 3.15 profiling status map

**Vintage:** 2026-09-24. Profiling-only. Runtime parents are on `main`; this file tracks bring-up state for agents and humans.

| PR | Role | State (as of vintage) |
| --- | --- | --- |
| [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | Native ABI / Echion layout | **merged** 2026-09-09 |
| [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) | Compile natives + gated CI | **merged** |
| [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | asyncio `sys.monitoring` on 3.15+; `wrap` below | **merged** 2026-09-22 |
| [#19207](https://github.com/DataDog/dd-trace-py/pull/19207) | prof-correctness gate on profiling PRs | **merged** |
| [#19861](https://github.com/DataDog/dd-trace-py/pull/19861) | Cython&lt;3.3; cp315 wheels optional | **merged** |
| [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | Runbook, registry, skill, fail-closed scripts | **open** (this) |
| [#20450](https://github.com/DataDog/dd-trace-py/pull/20450) | Require cp315 wheels; lib_injection schedule | **open** |
| [#20474](https://github.com/DataDog/dd-trace-py/pull/20474) | PEP 440 local versions in hermetic pip check | **open** |
| [#20478](https://github.com/DataDog/dd-trace-py/pull/20478) | ADR: profiling readiness for py-315 | **open** |

Related (not this stack tip): wrap trampoline [#19910](https://github.com/DataDog/dd-trace-py/pull/19910) (merged); wrapping context [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) (merged); IMAGE_TAG bump [#19936](https://github.com/DataDog/dd-trace-py/pull/19936) (merged).

## Layer table

| Layer | Meaning | Which PR |
| --- | --- | --- |
| Compiled | Native C++/Rust 3.15 ABI, cmake layout contracts | #19269 |
| Armed (build) | Collectors compile + riot/CI allow_failure | #19270 |
| Armed (runtime) | `sys.monitoring` asyncio on 3.15+; `wrap()` below | #19272 |
| Observable (docs/tooling) | Runbook, version registry, verify scripts | #19273 |
| Wheels required | cp315 required + lib_injection | #20450 |
| ADR | Readiness write-up | #20478 |

Do not attach DoE/AB results to #19273 (docs only).

## Validation legs

- Local: `scripts/verify_profiler_compatibility.py` + `scripts/run-profiling-tests` (default target from `scripts/profiles/profiling_versions.json`).
- prof-correctness: `python_*_3.15` jobs (S3-wheel path).
- Staging: smoke A/B → ai_gateway A/B (experimental staging_ab playbook). Preflight auth/signing/wheels before blaming the profiler.

## Agent entry

Use skill `migrate-profiling-new-cpython` for the next minor. Catalog: `docs/cpython-diffs/py315_pr_catalog.md`.
