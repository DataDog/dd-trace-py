# Python 3.15 profiling stack (5 PRs)

**Last updated:** 2026-08-27 — refreshed onto `main` (`98304b0694` / #19901). Not merge-ready.

Import degrade for 3.15 is on `main` via [#19724](https://github.com/DataDog/dd-trace-py/pull/19724). [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) (wrapping) stays on `main` and is not part of this stack. Official-support / riot / wheels PRs are separate.

## Merge order (bottom → top)

| # | PR | Branch | Base branch | Tip SHA (post-refresh) |
|---|-----|--------|-------------|------------------------|
| 1 | [#19267](https://github.com/DataDog/dd-trace-py/pull/19267) | `vlad/315-peripheral-compat` | `main` | `ee11c29203` |
| 2 | [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | `vlad/ddtracepy-315-profiling-native` | `vlad/315-peripheral-compat` | `2d20a3cd6d` |
| 3 | [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) | `vlad/ddtracepy-315-profiling-collectors` | `vlad/ddtracepy-315-profiling-native` | `b7a93de3b4` |
| 4 | [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | `vlad/ddtracepy-315-profiling-asyncio-monitoring` | `vlad/ddtracepy-315-profiling-collectors` | `6d9945ab78` |
| 5 | [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | `vlad/315-profiling-dev-tooling` | `vlad/ddtracepy-315-profiling-asyncio-monitoring` | this branch tip |

**Closed (absorbed earlier):** [#19268](https://github.com/DataDog/dd-trace-py/pull/19268) → #19269 · [#19271](https://github.com/DataDog/dd-trace-py/pull/19271) → #19270

**Not in this stack:** [#19254](https://github.com/DataDog/dd-trace-py/pull/19254) official support, riot #19880–#19906, wrap #17849, wheels.

## Layer table

| Layer | Meaning | Which PR |
| --- | --- | --- |
| Compiled into artifact | Native C++/Rust 3.15 ABI, cmake tests | #19269 |
| Armed at runtime | Collectors + setup.py native compile + riot/CI matrix | #19270 |
| Observable in product/Python | sys.monitoring asyncio path (wrapping unavailable) | #19272 |
| Docs / bring-up | Runbook, verify script, this file | #19273 |
| Peripheral (logging / FunctionStore) | Version warning + restore_all unwrap | #19267 |

## Validation legs (314 vs 315)

1. **prof-correctness** downstream gate — same wheel SHA, paired 3.14/3.15 scenarios
2. **DoE alloc isolated** — `mem_only=true`, mixed domain
3. **DoE full-stack** — all default collectors
4. **Staging soaks** — `rapid_python_http_smoke_test` + `ai_gateway` TD, then `ds-metrics-workers` temporal windows

Trigger gate + DoE after first green **native + collectors/CI** wheel (#19270 tip).
