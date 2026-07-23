# Manual steps for the py3.15 stack PRs (EMU blocks `gh pr edit`)

Branches are **rebased and force-pushed** (2026-07-23). Gab's commits are **unsigned** (not wrongly signed);
Vlad's commits are GPG-verified. `gh` cannot retarget bases or edit bodies from this EMU account — do the
steps below in the GitHub UI (or with a non-EMU token).

## Close duplicate

| PR | Action |
|----|--------|
| [#19248](https://github.com/DataDog/dd-trace-py/pull/19248) | **Close** — duplicate of [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) (`gab/315-wrapping-context` ≡ `chore/315-wrapping-context`) |

## Retarget base branch (required for incremental diffs)

| # | PR | Head branch | Set base to |
|---|-----|-------------|-------------|
| 1 | [#19247](https://github.com/DataDog/dd-trace-py/pull/19247) | `gab/315-monitoring-multiplexer` | `main` |
| 2 | [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) | `chore/315-wrapping-context` | `gab/315-monitoring-multiplexer` |
| 3 | [#19253](https://github.com/DataDog/dd-trace-py/pull/19253) | `vlad/315-ci-matrix` | `gab/315-wrapping-context` |
| 4 | [#19252](https://github.com/DataDog/dd-trace-py/pull/19252) | `vlad/315-ci-autoregen-lockfiles` | `vlad/315-ci-matrix` |
| 5 | [#19254](https://github.com/DataDog/dd-trace-py/pull/19254) | `vlad/315-official-support` | `vlad/315-ci-autoregen-lockfiles` |
| 6 | [#19255](https://github.com/DataDog/dd-trace-py/pull/19255) | `vlad/315-peripheral-compat` | `vlad/315-official-support` |
| 7 | [#19257](https://github.com/DataDog/dd-trace-py/pull/19257) | `vlad/profiling-native-test-install-subdir` | `vlad/315-peripheral-compat` |
| 8 | [#19250](https://github.com/DataDog/dd-trace-py/pull/19250) | `vlad/ddtracepy-315-profiling-native` | `vlad/profiling-native-test-install-subdir` |
| 9 | [#19251](https://github.com/DataDog/dd-trace-py/pull/19251) | `vlad/ddtracepy-315-profiling-collectors` | `vlad/ddtracepy-315-profiling-native` |
| 10 | [#19249](https://github.com/DataDog/dd-trace-py/pull/19249) | `vlad/ddtracepy-315-profiling-only` | `vlad/ddtracepy-315-profiling-collectors` |
| 11 | [#19256](https://github.com/DataDog/dd-trace-py/pull/19256) | `vlad/ddtracepy-315-profiling-asyncio-monitoring` | `vlad/ddtracepy-315-profiling-only` |
| 12 | [#19260](https://github.com/DataDog/dd-trace-py/pull/19260) | `vlad/315-profiling-dev-tooling` | `vlad/ddtracepy-315-profiling-asyncio-monitoring` |
| 13 | [#19258](https://github.com/DataDog/dd-trace-py/pull/19258) | `vlad/315-lib-injection-ssi` | `vlad/315-profiling-dev-tooling` |
| 14 | [#19259](https://github.com/DataDog/dd-trace-py/pull/19259) | `vlad/315-profiling-release-note` | `vlad/315-lib-injection-ssi` |

After retargeting, each PR should show **1 commit** (PR 11 shows **3** asyncio commits) vs its parent branch.

## Update PR description

Paste the body from `pr-bodies/NN-*.md` (first line: `**prev:** … | **next:** …`). Full index: [`DRAFT_PRS.md`](DRAFT_PRS.md).

Numbers are in [`pr-numbers.env`](pr-numbers.env). Re-run `./generate-pr-urls.sh` after any number change.

## Superseded closed PRs

Do not reopen: #18488, #18503, #18504, #17624, #18389 — replaced by #19257–#19256 above.
