# Python 3.15 profiling stack — status map

**Vintage:** Mon 2026-10-19 (EDT). Live PR states via `gh pr view` on that date.
**Do not treat tip SHAs in older revisions of this file as current.**

CPython `v3.15.0` final was tagged 2026-10-09. Native ABI, asyncio, collector,
and cp315 wheel work is on `main` via the merged PRs below. Official packaging
support is still pending (`requires-python` excludes 3.15; #19996 is the widen
draft). On `main`, `docker/.python-version` asks for `3.15.0`, but
`.gitlab/testrunner.yml` still pins an older `TESTRUNNER_IMAGE` digest and runs
`pyenv global … 3.15-dev`, so CI keeps using that image's `3.15-dev` build until
the pin moves (tracked in #21031). Docs closeout is the
thin Q3 PR (catalog + stack map + pointers). Suitespec 3.15 opt-in, version
registry, and verify tooling (`verify_profiler_compatibility.py`,
`run-profiling-tests`, baselines) live on #19273 (Q4). #19272 is **not** the
wrap lift (#19910 is on `main`).

## Current status (2026-10-19)

| PR | State | Role |
| --- | --- | --- |
| [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | **merged** | Native ABI / Echion layout / cmake contracts |
| [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | **merged** | asyncio `sys.monitoring` path on 3.15+ (`wrap()` fallback if registration fails) |
| [#19207](https://github.com/DataDog/dd-trace-py/pull/19207) | **merged** | prof-correctness gate on profiling PRs |
| [#19861](https://github.com/DataDog/dd-trace-py/pull/19861) | **merged** | Cython&lt;3.3 pin; cp315 wheels started optional |
| [#20450](https://github.com/DataDog/dd-trace-py/pull/20450) | **merged** | require cp315 wheels; schedule lib_injection on 3.15 |
| [#20671](https://github.com/DataDog/dd-trace-py/pull/20671) | **merged** | 3.15 shutdown stderr noise; memalloc test expectation |
| [#20947](https://github.com/DataDog/dd-trace-py/pull/20947) | **merged** | testrunner Dockerfile installs CPython 3.15.0 final (pyenv 2.8.9) |
| [#21029](https://github.com/DataDog/dd-trace-py/pull/21029) | **merged** | testrunner installs `libmpdec-dev` so 3.15 builds `_decimal` |
| [#21069](https://github.com/DataDog/dd-trace-py/pull/21069) | **merged** | testrunner arm64 platform check |
| [#20474](https://github.com/DataDog/dd-trace-py/pull/20474) | **open (draft)** | hermetic pip / PEP 440 local versions |
| [#19996](https://github.com/DataDog/dd-trace-py/pull/19996) | **open (draft)** | widen `requires-python` to include 3.15 |
| [#20478](https://github.com/DataDog/dd-trace-py/pull/20478) | **closed** | profiling readiness ADR |
| [#20814](https://github.com/DataDog/dd-trace-py/pull/20814) | **open** | Q3 catalog, stack map, thin bring-up pointers |
| [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | **open (draft)** | Q4 verify tooling + suitespec 3.15 matrix |

## Layer table

| Layer | Meaning | Which PR |
| --- | --- | --- |
| Compiled into artifact | Native C++/Rust 3.15 ABI, cmake tests | #19269 |
| Armed at runtime | Collectors + setup.py native compile + CI matrix | #19270 |
| Observable in product/Python | sys.monitoring asyncio path on 3.15+; `wrap()` below 3.15 and as fallback | #19272 |
| Test gating | prof-correctness on profiling PRs | #19207 |
| Wheels / packaging | optional → required cp315; Cython pin | #19861 → #20450 |
| Docs (Q3) | Catalog, stack map, thin pointers | #20814 |
| Verify / suitespec (Q4) | `verify_*` / suitespec 3.15 + locks / registry + baselines | #19273 |
| ADR | Readiness write-up | #20478 (closed) |

Short bring-up pointers: `docs/contributing-profiling-new-cpython.rst`.
Version answers: `docs/cpython-diffs/py315_pr_catalog.md`.
