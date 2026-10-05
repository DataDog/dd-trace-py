# Python 3.15 profiling stack — status map

**Vintage:** Mon 2026-10-05 (EDT). Live PR states via `gh pr view` on that date.
**Do not treat tip SHAs in older revisions of this file as current.**

#19273 is the Q4 verify + fuller tooling vehicle. Docs/suitespec closeout is
#20814 (Q3). Runtime is already on `main` via the merged PRs below. #19272 is
**not** the wrap lift (#19910 is on `main`).

After the split from #20814, this PR holds:
`scripts/verify_profiler_compatibility.py`, `scripts/run-profiling-tests`,
`scripts/profiles/compatibility_baselines.json`, the full
`profiling_versions.json` (incl. `checklist_template`), and
`tests/internal/test_profiler_compat_fail_closed.py`.

## Current status (2026-10-05)

| PR | State | Role |
| --- | --- | --- |
| [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | **merged** | Native ABI / Echion layout / cmake contracts |
| [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | **merged** | asyncio `sys.monitoring` path (wrap stays below 3.15) |
| [#19207](https://github.com/DataDog/dd-trace-py/pull/19207) | **merged** | prof-correctness gate on profiling PRs |
| [#19861](https://github.com/DataDog/dd-trace-py/pull/19861) | **merged** | Cython&lt;3.3 pin; cp315 wheels started optional |
| [#20450](https://github.com/DataDog/dd-trace-py/pull/20450) | **merged** | require cp315 wheels; schedule lib_injection on 3.15 |
| [#20474](https://github.com/DataDog/dd-trace-py/pull/20474) | **open** | hermetic pip / PEP 440 local versions |
| [#20478](https://github.com/DataDog/dd-trace-py/pull/20478) | **closed** | profiling readiness ADR |
| [#20814](https://github.com/DataDog/dd-trace-py/pull/20814) | **open (draft)** | Q3 catalog, stack map, thin pointers, suitespec |
| [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | **open (draft)** | Q4 verify tooling + fuller runbook (this PR) |

## Layer table

| Layer | Meaning | Which PR |
| --- | --- | --- |
| Compiled into artifact | Native C++/Rust 3.15 ABI, cmake tests | #19269 |
| Armed at runtime | Collectors + setup.py native compile + CI matrix | #19270 |
| Observable in product/Python | sys.monitoring asyncio path; `wrap()` stays below 3.15 | #19272 |
| Test gating | prof-correctness on profiling PRs | #19207 |
| Wheels / packaging | optional → required cp315; Cython pin | #19861 → #20450 |
| Docs / suitespec (Q3) | Catalog, thin pointers + registry, suitespec 3.15 | #20814 |
| Verify / tooling (Q4) | `verify_*` / `run-profiling-tests` / full registry + baselines | #19273 |
| ADR | Readiness write-up | #20478 (closed) |

Validation gates, DoE/TD attach rules, and the sign-off chain live in
`docs/contributing-profiling-new-cpython.rst`.
