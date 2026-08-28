# Manual steps for the py3.15 profiling stack PRs

Linear stack (2026-08-27): five PRs, each based on the **previous branch**.
[#17849](https://github.com/DataDog/dd-trace-py/pull/17849) wrapping stays on `main` — do not retarget it onto this stack.

## Active stack

| # | PR | Head branch | Base branch |
|---|-----|-------------|-------------|
| 1 | [#19267](https://github.com/DataDog/dd-trace-py/pull/19267) | `vlad/315-peripheral-compat` | `main` |
| 2 | [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | `vlad/ddtracepy-315-profiling-native` | `vlad/315-peripheral-compat` |
| 3 | [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) | `vlad/ddtracepy-315-profiling-collectors` | `vlad/ddtracepy-315-profiling-native` |
| 4 | [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | `vlad/ddtracepy-315-profiling-asyncio-monitoring` | `vlad/ddtracepy-315-profiling-collectors` |
| 5 | [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | `vlad/315-profiling-dev-tooling` | `vlad/ddtracepy-315-profiling-asyncio-monitoring` |

Numbers are in [`pr-numbers.env`](pr-numbers.env). Canonical table: [`PROFILING_STACK.md`](PROFILING_STACK.md).

## Outside this stack (do not restack onto these five)

- [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) wrapping — approved, base=`main`
- Riot / official-support / wheels (#19254, #19880–#19906, #19861, …)
- Follow-ups: lib-injection SSI, profiling release note (see [`TRACKING.md`](TRACKING.md))

## Superseded (do not use)

Absorbed: #19268 → #19269, #19271 → #19270.

Older 14-PR numbering (gab multiplexer, ci-matrix, official-support as tip) is historical. `DRAFT_PRS.md` / `rebuild-stack.sh` still describe that older chain.
