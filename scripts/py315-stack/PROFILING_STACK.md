# Python 3.15 profiling stack — status map

**Vintage:** Wed 2026-09-30 (EDT). Live PR states via `gh pr view` on that date.
**Do not treat tip SHAs in older revisions of this file as current.**

This file tracks profiling bring-up for CPython 3.15 and the tooling that
makes the next minor cheaper. Runtime code lives on `main` via the merged
parents below; #19273 is docs/tooling only.

## Current status (2026-09-30)

| PR | State | Role |
| --- | --- | --- |
| [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | **merged** | Native ABI / Echion layout / cmake contracts |
| [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | **merged** | asyncio `sys.monitoring` path (wrap stays below 3.15) |
| [#19207](https://github.com/DataDog/dd-trace-py/pull/19207) | **merged** | prof-correctness gate on profiling PRs |
| [#19861](https://github.com/DataDog/dd-trace-py/pull/19861) | **merged** | Cython&lt;3.3 pin; cp315 wheels started optional |
| [#20450](https://github.com/DataDog/dd-trace-py/pull/20450) | **merged** | require cp315 wheels; schedule lib_injection on 3.15 |
| [#20474](https://github.com/DataDog/dd-trace-py/pull/20474) | **open** | hermetic pip / PEP 440 local versions |
| [#20478](https://github.com/DataDog/dd-trace-py/pull/20478) | **open (draft)** | profiling readiness ADR |
| [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | **open (draft)** | runbook + version registry + migration skill (this PR) |

Version registry: `scripts/profiles/profiling_versions.json`
(scaffold: `python scripts/verify_profiler_compatibility.py --scaffold 3.16`).

Orchestrator skill: `.claude/skills/migrate-profiling-new-cpython/SKILL.md`
Runbook: `docs/contributing-profiling-new-cpython.rst`

## Layer table

| Layer | Meaning | Which PR |
| --- | --- | --- |
| Compiled into artifact | Native C++/Rust 3.15 ABI, cmake tests | #19269 |
| Armed at runtime | Collectors + setup.py native compile + riot/CI matrix | #19270 |
| Observable in product/Python | sys.monitoring asyncio path; `wrap()` stays below 3.15 | #19272 |
| Test gating | prof-correctness on profiling PRs | #19207 |
| Wheels / packaging | optional → required cp315; Cython pin | #19861 → #20450 |
| Docs / bring-up | Runbook, verify script, version registry, this file | #19273 |
| ADR | Readiness write-up | #20478 (open) |

#19272 is **not** the wrap lift (#19910 is on `main`). Do not attach DoE/AB to #19273.

## Validation legs (314 vs 315)

Version is the knob; wheel SHA stays fixed. Both 3.14 and 3.15 DoE use the **same #19272 tip**.

- **Wrap-free DoE** (alloc isolated / full-stack / bytearray): **#19272** tip only.
- **Wrap-sensitive TD** (`ai_gateway`): needs #19910 **in the wheel** (on `main`).
- Attach results to **#19269 / #19270 / #19272**. Never #19273.
- prof-correctness 3.15 is **S3-wheel only** (`install.sh`). Residual packaging traps are tracked in the runbook / #20474.

Sign-off: prof-correctness → DoE alloc isolated → full-stack → `rapid_python_http_smoke_test` TD → `ai_gateway` TD (after wrap is in the wheel) → `ds-metrics-workers` temporal soak.
