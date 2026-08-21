# Python 3.15 profiling stack (6 draft PRs)

**Last updated:** 2026-08-21 — consolidated from 10 → 6 PRs (#19268→#19269, #19271→#19270, #19274+#19275→#19254).

Import degrade for 3.15 is on `main` via [#19724](https://github.com/DataDog/dd-trace-py/pull/19724); this stack starts at peripheral compat.

## Merge order (bottom → top)

| # | PR | Branch | Base branch | Tip SHA (post-consolidation) |
|---|-----|--------|-------------|------------------------------|
| 1 | [#19267](https://github.com/DataDog/dd-trace-py/pull/19267) | `vlad/315-peripheral-compat` | `main` | `107d29b75e` |
| 2 | [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | `vlad/ddtracepy-315-profiling-native` | `vlad/315-peripheral-compat` | `0fb56d5ca4` |
| 3 | [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) | `vlad/ddtracepy-315-profiling-collectors` | `vlad/ddtracepy-315-profiling-native` | `ee28a3ffc2` |
| 4 | [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | `vlad/ddtracepy-315-profiling-asyncio-monitoring` | `vlad/ddtracepy-315-profiling-collectors` | `082c37a1cf` |
| 5 | [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | `vlad/315-profiling-dev-tooling` | `vlad/ddtracepy-315-profiling-asyncio-monitoring` | `ba0e2bf2c6` |
| 6 | [#19254](https://github.com/DataDog/dd-trace-py/pull/19254) | `vlad/315-official-support` | `vlad/315-profiling-dev-tooling` | `75ed3e3f91` |

**Closed (absorbed):** [#19268](https://github.com/DataDog/dd-trace-py/pull/19268) → #19269 · [#19271](https://github.com/DataDog/dd-trace-py/pull/19271) → #19270 · [#19274](https://github.com/DataDog/dd-trace-py/pull/19274) + [#19275](https://github.com/DataDog/dd-trace-py/pull/19275) → #19254

## Validation legs (314 vs 315)

1. **prof-correctness** downstream gate ([#166](https://github.com/DataDog/prof-correctness/pull/166)–[#172](https://github.com/DataDog/prof-correctness/pull/172)) — same wheel SHA, paired 3.14/3.15 scenarios
2. **DoE alloc isolated** — `mem_only=true`, mixed domain
3. **DoE full-stack** — all default collectors
4. **Staging soaks** — `rapid_python_http_smoke_test` + `ai_gateway` TD, then `ds-metrics-workers` temporal windows

Trigger gate + DoE after first green **native + collectors/CI** wheel (#19270 tip).

## Rebuild

```bash
./scripts/py315-stack/rebuild-stack.sh   # after editing cherry-pick list if needed
```
