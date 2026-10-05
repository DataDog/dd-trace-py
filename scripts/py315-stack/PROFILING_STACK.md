# Python 3.15 profiling stack — status map

**Vintage:** Mon 2026-10-05 (EDT). Live PR states via `gh pr view` on that date.
**Do not treat tip SHAs in older revisions of this file as current.**

Runtime is already on `main` via the merged PRs below. Docs closeout is the
thin Q3 PR (catalog + stack map + pointers + thin registry). Suitespec 3.15
opt-in + verify tooling (`verify_profiler_compatibility.py`,
`run-profiling-tests`, baselines, full registry checklist) live on #19273
(Q4). #19272 is **not** the wrap lift (#19910 is on `main`).

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
| [#20814](https://github.com/DataDog/dd-trace-py/pull/20814) | **open (draft)** | Q3 catalog, stack map, thin bring-up pointers |
| [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | **open (draft)** | Q4 verify tooling + suitespec 3.15 matrix |

## Layer table

| Layer | Meaning | Which PR |
| --- | --- | --- |
| Compiled into artifact | Native C++/Rust 3.15 ABI, cmake tests | #19269 |
| Armed at runtime | Collectors + setup.py native compile + CI matrix | #19270 |
| Observable in product/Python | sys.monitoring asyncio path; `wrap()` stays below 3.15 | #19272 |
| Test gating | prof-correctness on profiling PRs | #19207 |
| Wheels / packaging | optional → required cp315; Cython pin | #19861 → #20450 |
| Docs (Q3) | Catalog, stack map, thin pointers + registry | #20814 |
| Verify / suitespec (Q4) | `verify_*` / suitespec 3.15 + locks / full registry + baselines | #19273 |
| ADR | Readiness write-up | #20478 (closed) |

Short bring-up pointers: `docs/contributing-profiling-new-cpython.rst`.
Version answers: `docs/cpython-diffs/py315_pr_catalog.md`.
