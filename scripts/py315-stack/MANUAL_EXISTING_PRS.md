# Manual steps for the py3.15 stack PRs

Linear stack (2026-07-24): each PR bases on the **previous branch**, not an integration branch.
Official 3.15 packaging support ([#19254](https://github.com/DataDog/dd-trace-py/pull/19254)) is **PR 14 / stack tip**.

## Active stack

| # | PR | Head branch | Base branch |
|---|-----|-------------|-------------|
| 1 | [#19247](https://github.com/DataDog/dd-trace-py/pull/19247) | `gab/315-monitoring-multiplexer` | `main` |
| 2 | [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) | `chore/315-wrapping-context` | `gab/315-monitoring-multiplexer` |
| 3 | [#19253](https://github.com/DataDog/dd-trace-py/pull/19253) | `vlad/315-ci-matrix` | `gab/315-wrapping-context` |
| 4 | [#19252](https://github.com/DataDog/dd-trace-py/pull/19252) | `vlad/315-ci-autoregen-lockfiles` | `vlad/315-ci-matrix` |
| 5 | [#19267](https://github.com/DataDog/dd-trace-py/pull/19267) | `vlad/315-peripheral-compat` | `vlad/315-ci-autoregen-lockfiles` |
| 6 | [#19268](https://github.com/DataDog/dd-trace-py/pull/19268) | `vlad/profiling-native-test-install-subdir` | `vlad/315-peripheral-compat` |
| 7 | [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | `vlad/ddtracepy-315-profiling-native` | `vlad/profiling-native-test-install-subdir` |
| 8 | [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) | `vlad/ddtracepy-315-profiling-collectors` | `vlad/ddtracepy-315-profiling-native` |
| 9 | [#19271](https://github.com/DataDog/dd-trace-py/pull/19271) | `vlad/ddtracepy-315-profiling-only` | `vlad/ddtracepy-315-profiling-collectors` |
| 10 | [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | `vlad/ddtracepy-315-profiling-asyncio-monitoring` | `vlad/ddtracepy-315-profiling-only` |
| 11 | [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | `vlad/315-profiling-dev-tooling` | `vlad/ddtracepy-315-profiling-asyncio-monitoring` |
| 12 | [#19274](https://github.com/DataDog/dd-trace-py/pull/19274) | `vlad/315-lib-injection-ssi` | `vlad/315-profiling-dev-tooling` |
| 13 | [#19275](https://github.com/DataDog/dd-trace-py/pull/19275) | `vlad/315-profiling-release-note` | `vlad/315-lib-injection-ssi` |
| 14 | [#19254](https://github.com/DataDog/dd-trace-py/pull/19254) | `vlad/315-official-support` | `vlad/315-profiling-release-note` |

Numbers are in [`pr-numbers.env`](pr-numbers.env). Re-run [`generate-pr-urls.sh`](generate-pr-urls.sh) after any number change.

## Superseded (do not use)

These were merged into the old `vlad/315-official-support` integration branch with the wrong base. Replaced by #19267–#19275:

#19255, #19257, #19250, #19251, #19249, #19256, #19260, #19258, #19259

Also do not reopen: #18488, #18503, #18504, #17624, #18389, #19248 (duplicate of #17849).
