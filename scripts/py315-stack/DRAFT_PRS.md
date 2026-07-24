# Python 3.15 stack — draft PRs (1–14)

EMU blocks `gh pr create` / `gh pr edit`. Run [`sync-pr-numbers.sh`](sync-pr-numbers.sh) then [`open-all-draft-prs.sh`](open-all-draft-prs.sh).
Set numbers: `./sync-pr-numbers.sh PR1=19250 PR6=19255 ...` (preserves existing, fills gaps from GitHub).

## 1/14 — `gab/315-monitoring-multiplexer` → `main`

**Existing PR:** [#19247](https://github.com/DataDog/dd-trace-py/pull/19247) — set base to `main`, mark draft, paste body from [`pr-bodies/01-gab-315-monitoring-multiplexer.md`](pr-bodies/01-gab-315-monitoring-multiplexer.md).

**prev:** — | **next:** [#17849](https://github.com/DataDog/dd-trace-py/pull/17849)

<details><summary>PR body (copy)</summary>

**prev:** — | **next:** [#17849](https://github.com/DataDog/dd-trace-py/pull/17849)

## Summary

Introduces `ddtrace.internal.monitoring` — shared `sys.monitoring` multiplexer (Gab). Split from #17849.


## Test plan

- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch


</details>

## 2/14 — `chore/315-wrapping-context` → `gab/315-monitoring-multiplexer`

**Existing PR:** [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) — set base to `gab/315-monitoring-multiplexer`, mark draft, paste body from [`pr-bodies/02-chore-315-wrapping-context.md`](pr-bodies/02-chore-315-wrapping-context.md).

**prev:** [#19247](https://github.com/DataDog/dd-trace-py/pull/19247) | **next:** [#19253](https://github.com/DataDog/dd-trace-py/pull/19253)

<details><summary>PR body (copy)</summary>

**prev:** [#19247](https://github.com/DataDog/dd-trace-py/pull/19247) | **next:** [#19253](https://github.com/DataDog/dd-trace-py/pull/19253)

## Summary

Wrapping context + bytecode_injection for 3.15 (Gab + await/send fix).


Retarget existing **#17849** to base `gab/315-monitoring-multiplexer`.

## Test plan

- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch


</details>

## 3/14 — `vlad/315-ci-matrix` → `gab/315-wrapping-context`

**Existing PR:** [#19253](https://github.com/DataDog/dd-trace-py/pull/19253) — set base to `gab/315-wrapping-context`, mark draft, paste body from [`pr-bodies/03-vlad-315-ci-matrix.md`](pr-bodies/03-vlad-315-ci-matrix.md).

**prev:** [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) | **next:** [#19252](https://github.com/DataDog/dd-trace-py/pull/19252)

<details><summary>PR body (copy)</summary>

**prev:** [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) | **next:** [#19252](https://github.com/DataDog/dd-trace-py/pull/19252)

## Summary

Riotfile 3.15, docker testrunner, suite gating.


## Test plan

- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch


</details>

## 4/14 — `vlad/315-ci-autoregen-lockfiles` → `vlad/315-ci-matrix`

**Existing PR:** [#19252](https://github.com/DataDog/dd-trace-py/pull/19252) — set base to `vlad/315-ci-matrix`, mark draft, paste body from [`pr-bodies/04-vlad-315-ci-autoregen-lockfiles.md`](pr-bodies/04-vlad-315-ci-autoregen-lockfiles.md).

**prev:** [#19253](https://github.com/DataDog/dd-trace-py/pull/19253) | **next:** [#19267](https://github.com/DataDog/dd-trace-py/pull/19267)

<details><summary>PR body (copy)</summary>

**prev:** [#19253](https://github.com/DataDog/dd-trace-py/pull/19253) | **next:** [#19267](https://github.com/DataDog/dd-trace-py/pull/19267)

## Summary

Self-healing lockfile drift on PR branches.


## Test plan

- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch


</details>

## 5/14 — `vlad/315-peripheral-compat` → `vlad/315-ci-autoregen-lockfiles`

**Existing PR:** [#19267](https://github.com/DataDog/dd-trace-py/pull/19267) — set base to `vlad/315-ci-autoregen-lockfiles`, mark draft, paste body from [`pr-bodies/05-vlad-315-peripheral-compat.md`](pr-bodies/05-vlad-315-peripheral-compat.md).

**prev:** [#19252](https://github.com/DataDog/dd-trace-py/pull/19252) | **next:** [#19268](https://github.com/DataDog/dd-trace-py/pull/19268)

<details><summary>PR body (copy)</summary>

**prev:** [#19252](https://github.com/DataDog/dd-trace-py/pull/19252) | **next:** [#19268](https://github.com/DataDog/dd-trace-py/pull/19268)

## Summary

Graceful degradation + test skips outside wrapping core.


Replaces merged **#19255** (wrong integration-branch base).

## Test plan

- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch


</details>

## 6/14 — `vlad/profiling-native-test-install-subdir` → `vlad/315-peripheral-compat`

**Existing PR:** [#19268](https://github.com/DataDog/dd-trace-py/pull/19268) — set base to `vlad/315-peripheral-compat`, mark draft, paste body from [`pr-bodies/06-vlad-profiling-native-test-install-subdir.md`](pr-bodies/06-vlad-profiling-native-test-install-subdir.md).

**prev:** [#19267](https://github.com/DataDog/dd-trace-py/pull/19267) | **next:** [#19269](https://github.com/DataDog/dd-trace-py/pull/19269)

<details><summary>PR body (copy)</summary>

**prev:** [#19267](https://github.com/DataDog/dd-trace-py/pull/19267) | **next:** [#19269](https://github.com/DataDog/dd-trace-py/pull/19269)

## Summary

INSTALL_SUBDIR for py3.15 native tests.


Replaces merged **#19257**.

## Test plan

- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch


</details>

## 7/14 — `vlad/ddtracepy-315-profiling-native` → `vlad/profiling-native-test-install-subdir`

**Existing PR:** [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) — set base to `vlad/profiling-native-test-install-subdir`, mark draft, paste body from [`pr-bodies/07-vlad-ddtracepy-315-profiling-native.md`](pr-bodies/07-vlad-ddtracepy-315-profiling-native.md).

**prev:** [#19268](https://github.com/DataDog/dd-trace-py/pull/19268) | **next:** [#19270](https://github.com/DataDog/dd-trace-py/pull/19270)

<details><summary>PR body (copy)</summary>

**prev:** [#19268](https://github.com/DataDog/dd-trace-py/pull/19268) | **next:** [#19270](https://github.com/DataDog/dd-trace-py/pull/19270)

## Summary

Native profiling py3.15 ABI (Echion frame state, cmake).


Replaces merged **#19250**.

## Test plan

- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch


</details>

## 8/14 — `vlad/ddtracepy-315-profiling-collectors` → `vlad/ddtracepy-315-profiling-native`

**Existing PR:** [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) — set base to `vlad/ddtracepy-315-profiling-native`, mark draft, paste body from [`pr-bodies/08-vlad-ddtracepy-315-profiling-collectors.md`](pr-bodies/08-vlad-ddtracepy-315-profiling-collectors.md).

**prev:** [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | **next:** [#19271](https://github.com/DataDog/dd-trace-py/pull/19271)

<details><summary>PR body (copy)</summary>

**prev:** [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | **next:** [#19271](https://github.com/DataDog/dd-trace-py/pull/19271)

## Summary

Collector updates for 3.15.


Replaces merged **#19251**.

## Test plan

- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch


</details>

## 9/14 — `vlad/ddtracepy-315-profiling-only` → `vlad/ddtracepy-315-profiling-collectors`

**Existing PR:** [#19271](https://github.com/DataDog/dd-trace-py/pull/19271) — set base to `vlad/ddtracepy-315-profiling-collectors`, mark draft, paste body from [`pr-bodies/09-vlad-ddtracepy-315-profiling-only.md`](pr-bodies/09-vlad-ddtracepy-315-profiling-only.md).

**prev:** [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) | **next:** [#19272](https://github.com/DataDog/dd-trace-py/pull/19272)

<details><summary>PR body (copy)</summary>

**prev:** [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) | **next:** [#19272](https://github.com/DataDog/dd-trace-py/pull/19272)

## Summary

Profiling CI matrix and setup.py gating.


Replaces merged **#19249**.

## Test plan

- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch


</details>

## 10/14 — `vlad/ddtracepy-315-profiling-asyncio-monitoring` → `vlad/ddtracepy-315-profiling-only`

**Existing PR:** [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) — set base to `vlad/ddtracepy-315-profiling-only`, mark draft, paste body from [`pr-bodies/10-vlad-ddtracepy-315-profiling-asyncio-monitoring.md`](pr-bodies/10-vlad-ddtracepy-315-profiling-asyncio-monitoring.md).

**prev:** [#19271](https://github.com/DataDog/dd-trace-py/pull/19271) | **next:** [#19273](https://github.com/DataDog/dd-trace-py/pull/19273)

<details><summary>PR body (copy)</summary>

**prev:** [#19271](https://github.com/DataDog/dd-trace-py/pull/19271) | **next:** [#19273](https://github.com/DataDog/dd-trace-py/pull/19273)

## Summary

Replace bytecode wrapping with `sys.monitoring` in `_asyncio.py`.


Replaces merged **#19256**.

## Test plan

- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch


</details>

## 11/14 — `vlad/315-profiling-dev-tooling` → `vlad/ddtracepy-315-profiling-asyncio-monitoring`

**Existing PR:** [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) — set base to `vlad/ddtracepy-315-profiling-asyncio-monitoring`, mark draft, paste body from [`pr-bodies/11-vlad-315-profiling-dev-tooling.md`](pr-bodies/11-vlad-315-profiling-dev-tooling.md).

**prev:** [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | **next:** [#19274](https://github.com/DataDog/dd-trace-py/pull/19274)

<details><summary>PR body (copy)</summary>

**prev:** [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | **next:** [#19274](https://github.com/DataDog/dd-trace-py/pull/19274)

## Summary

Profiling bring-up scripts, compatibility baselines, Echion migration runbook (GAP-03).


Replaces merged **#19260**.

## Test plan

- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch


</details>

## 12/14 — `vlad/315-lib-injection-ssi` → `vlad/315-profiling-dev-tooling`

**Existing PR:** [#19274](https://github.com/DataDog/dd-trace-py/pull/19274) — set base to `vlad/315-profiling-dev-tooling`, mark draft, paste body from [`pr-bodies/12-vlad-315-lib-injection-ssi.md`](pr-bodies/12-vlad-315-lib-injection-ssi.md).

**prev:** [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | **next:** [#19275](https://github.com/DataDog/dd-trace-py/pull/19275)

<details><summary>PR body (copy)</summary>

**prev:** [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | **next:** [#19275](https://github.com/DataDog/dd-trace-py/pull/19275)

## Summary

SSI allow-list + wheel download for 3.15 auto-instrumentation (GAP-01). Closes #17813.


Replaces merged **#19258**.

## Test plan

- [ ] `scripts/run-profiling-tests --check-only` passes on 3.15 (when available)
- [ ] Docs build / link check


</details>

## 13/14 — `vlad/315-profiling-release-note` → `vlad/315-lib-injection-ssi`

**Existing PR:** [#19275](https://github.com/DataDog/dd-trace-py/pull/19275) — set base to `vlad/315-lib-injection-ssi`, mark draft, paste body from [`pr-bodies/13-vlad-315-profiling-release-note.md`](pr-bodies/13-vlad-315-profiling-release-note.md).

**prev:** [#19274](https://github.com/DataDog/dd-trace-py/pull/19274) | **next:** [#19254](https://github.com/DataDog/dd-trace-py/pull/19254)

<details><summary>PR body (copy)</summary>

**prev:** [#19274](https://github.com/DataDog/dd-trace-py/pull/19274) | **next:** [#19254](https://github.com/DataDog/dd-trace-py/pull/19254)

## Summary

Customer-facing Reno fragment for profiling on 3.15 (GAP-02).


Replaces merged **#19259**.

## Test plan

- [ ] lib-injection CI green


</details>

## 14/14 — `vlad/315-official-support` → `vlad/315-profiling-release-note`

**Existing PR:** [#19254](https://github.com/DataDog/dd-trace-py/pull/19254) — set base to `vlad/315-profiling-release-note`, mark draft, paste body from [`pr-bodies/14-vlad-315-official-support.md`](pr-bodies/14-vlad-315-official-support.md).

**prev:** [#19275](https://github.com/DataDog/dd-trace-py/pull/19275) | **next:** —

<details><summary>PR body (copy)</summary>

**prev:** [#19275](https://github.com/DataDog/dd-trace-py/pull/19275) | **next:** —

## Summary

pyproject.toml, requirements.csv, riot lockfiles. **Stack tip — merges last.**


## Test plan

- [ ] `riot run reno` validates fragment
- [ ] Merge with PR that lifts profiling native gate for 3.15


</details>

