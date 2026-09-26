# Python 3.15 profiling PR catalog

**Vintage:** 2026-09-24. Author `vlad-scherbich`, created ≥2026-03-01, plus `#17849` (P403n1x87).
**Scope:** Continuous Profiler bring-up for CPython 3.15. Integration test-enable PRs are one aggregate row.
**Method:** month-by-month `gh search prs` / `gh pr list` plus `gh pr view`. `#17817` does not exist. `images#11732` is cited by a merged commit message but the GitHub API returns 404 — do not link it. `dd-source` and `system-tests` have no in-scope author PRs in this window (local engraver branches only).

Feeds the runbook, `migrate-profiling-new-cpython`, and (later) `cpython_delta` ground truth. Canonical landing PRs are the ones that merged; early stack attempts are one-line lessons.

---

## Alpha — natives, layout, PyO3

| PR | State | Category | Trigger | Expected? | Automate |
| --- | --- | --- | --- | --- | --- |
| [dd-trace-py#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | MERGED | native ABI | `PyFrameState` / `FRAME_OWNED_BY_CSTACK`; `test_frame_state_315` | expected | cpython_delta |
| [dd-trace-py#19270](https://github.com/DataDog/dd-trace-py/pull/19270) | MERGED | build/CI | compile natives + allow_failure job | expected | script/template |
| [dd-trace-py#19257](https://github.com/DataDog/dd-trace-py/pull/19257) | MERGED | build/CI | native test install subdirs | expected | script/template |
| [dd-trace-py#18429](https://github.com/DataDog/dd-trace-py/pull/18429) | MERGED | native ABI | pyo3 0.28 crashtracker / tracer_flare | expected | script/template |
| [dd-trace-py#19903](https://github.com/DataDog/dd-trace-py/pull/19903) | MERGED | native ABI | limited-API `PyContextVar_*` | surprise | cpython_delta |
| [libdatadog#1847](https://github.com/DataDog/libdatadog/pull/1847) | MERGED | lockfiles | pyo3 0.28 | expected | script/template |
| [libdatadog#2491](https://github.com/DataDog/libdatadog/pull/2491) | MERGED | lockfiles | pyo3 0.29 | expected | script/template |
| [dd-trace-py#17710](https://github.com/DataDog/dd-trace-py/pull/17710) | MERGED | build/CI | 3.15 alpha/dev interpreters; lockfile job OOM | expected | script/template |
| [dd-trace-py#18094](https://github.com/DataDog/dd-trace-py/pull/18094) | MERGED | packaging | fence `requires-python` `<3.15` while natives immature | expected | script/template |

**Automate for 3.16:** `find-cpython-usage` → `compare-cpython-versions` at first alpha; `--scaffold`; pyo3 bump in libdatadog before dd-trace-py.

---

## Beta — asyncio, wrapping, collectors

| PR | State | Category | Trigger | Expected? | Automate |
| --- | --- | --- | --- | --- | --- |
| [dd-trace-py#19247](https://github.com/DataDog/dd-trace-py/pull/19247) | MERGED | asyncio hook | `sys.monitoring` multiplexer | expected | cpython_delta |
| [dd-trace-py#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | MERGED | asyncio hook | profiling asyncio via monitoring, not `wrap()`, on 3.15+ | surprise (path) | human judgment |
| [dd-trace-py#19910](https://github.com/DataDog/dd-trace-py/pull/19910) | MERGED | wrapping | re-enable `wrap()` trampoline (PROF-15852) | rework | human judgment |
| [dd-trace-py#17849](https://github.com/DataDog/dd-trace-py/pull/17849) | MERGED | wrapping | wrapping context (P403n1x87) | expected | human judgment |
| [dd-trace-py#19724](https://github.com/DataDog/dd-trace-py/pull/19724) | MERGED | packaging | no crash when wrap unavailable | surprise | skill checklist |
| [dd-trace-py#19267](https://github.com/DataDog/dd-trace-py/pull/19267) | MERGED | docs | logging version warning | expected | script/template |
| [dd-trace-py#20397](https://github.com/DataDog/dd-trace-py/pull/20397) | MERGED | docs | remaining `sys.version_info` gates | expected | skill checklist |

**Automate for 3.16:** probe `wrap()` vs `sys.monitoring` event IDs each beta; keep the multiplexer shared with tracing.

---

## RC — images, wheels, Cython

| PR | State | Category | Trigger | Expected? | Automate |
| --- | --- | --- | --- | --- | --- |
| [images#9814](https://github.com/DataDog/images/pull/9814) | MERGED | wheels/images | mirror manylinux cp315 (2026.05.13) | expected | script/template |
| [images#9815](https://github.com/DataDog/images/pull/9815) | MERGED | wheels/images | dd-trace-py base bump after #9814 | expected | script/template |
| [dd-trace-py#17959](https://github.com/DataDog/dd-trace-py/pull/17959) | MERGED | wheels/images | unblock cp315 wheels via that bump | expected | script/template |
| [images#11355](https://github.com/DataDog/images/pull/11355) | MERGED | wheels/images | mirror 2026.08.04 rc1 | expected | script/template |
| [images#11356](https://github.com/DataDog/images/pull/11356) | MERGED | wheels/images | consumer bump (PROF-15844) | expected | script/template |
| [dd-trace-py#19936](https://github.com/DataDog/dd-trace-py/pull/19936) | MERGED | wheels/images | wheel-builder `IMAGE_TAG`s | expected | script/template |
| [dd-trace-py#19861](https://github.com/DataDog/dd-trace-py/pull/19861) | MERGED | lockfiles | Cython `<3.3`; optional cp315 wheels | surprise | script/template |
| [dd-trace-py#19907](https://github.com/DataDog/dd-trace-py/pull/19907) | MERGED | wheels/images | 3.15-dev testrunner + Cython pin | expected | script/template |
| [dd-trace-py#20157](https://github.com/DataDog/dd-trace-py/pull/20157) | MERGED | wheels/images | skip 3.15 dep-wheel download until image mirrored | surprise | human judgment |
| [dd-trace-py#19880](https://github.com/DataDog/dd-trace-py/pull/19880) | MERGED | packaging | withhold cp315 from PyPI until GA | expected | script/template |
| [dd-trace-py#20450](https://github.com/DataDog/dd-trace-py/pull/20450) | OPEN | wheels/images | require cp315 wheels; lib_injection | expected | script/template |

**Automate for 3.16:** mirror then consumer bump within days of each RC; pin the hermetic interpreter to the exact prerelease; start wheels optional.

---

## Final — SSI / packaging / docs

| PR | State | Category | Trigger | Expected? | Automate |
| --- | --- | --- | --- | --- | --- |
| [dd-trace-py#19258](https://github.com/DataDog/dd-trace-py/pull/19258) | MERGED | packaging/SSI | lib-injection SSI slice | expected | script/template |
| [dd-trace-py#19843](https://github.com/DataDog/dd-trace-py/pull/19843) | MERGED | packaging/SSI | SSI max 3.16 + pre-stage 3.15 | expected | script/template |
| [dd-trace-py#19942](https://github.com/DataDog/dd-trace-py/pull/19942) | MERGED | packaging/SSI | widen `requires-python` | expected | script/template |
| [dd-trace-py#19943](https://github.com/DataDog/dd-trace-py/pull/19943) | MERGED | packaging/SSI | supported-versions job includes 3.15 | expected | script/template |
| [dd-trace-py#19911](https://github.com/DataDog/dd-trace-py/pull/19911) | MERGED | packaging/SSI | unsupported-version error shows running CPython | expected | script/template |
| [dd-trace-py#19259](https://github.com/DataDog/dd-trace-py/pull/19259) | MERGED | docs | profiling 3.15 release note | expected | skill checklist |
| [dd-trace-py#20474](https://github.com/DataDog/dd-trace-py/pull/20474) | OPEN | packaging | PEP 440 local versions | surprise | human judgment |
| [dd-trace-py#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | OPEN | docs | this runbook / registry / skill | expected | — |
| [dd-trace-py#20478](https://github.com/DataDog/dd-trace-py/pull/20478) | OPEN | docs | ADR | expected | — |
| [dd-trace-py#17791](https://github.com/DataDog/dd-trace-py/pull/17791) | MERGED | docs | integration-bump PR template | expected | generalize to 3.X |

**Automate for 3.16:** SSI/OCI only at final. Sequence `requires-python`, classifier, SSI max, then lib-injection.

---

## Correctness + staging

| PR | State | Category | Trigger | Expected? | Automate |
| --- | --- | --- | --- | --- | --- |
| [dd-trace-py#19207](https://github.com/DataDog/dd-trace-py/pull/19207) | MERGED | test gating | prof-correctness required on py-315 profiling PRs | expected | script/template |
| [dd-trace-py#20444](https://github.com/DataDog/dd-trace-py/pull/20444) | MERGED | test gating | S3 wheel poll 60m | expected | script/template |
| [prof-correctness#166](https://github.com/DataDog/prof-correctness/pull/166)–[#172](https://github.com/DataDog/prof-correctness/pull/172) | MERGED | correctness | core / live_heap / cpu / stack / cross-cutting gates | expected | scenario template |
| [prof-correctness#189](https://github.com/DataDog/prof-correctness/pull/189), [#190](https://github.com/DataDog/prof-correctness/pull/190), [#214](https://github.com/DataDog/prof-correctness/pull/214) | MERGED | correctness | margin retune after 314 vs 315 drift | expected | regenerate from artifacts |
| [prof-correctness#165](https://github.com/DataDog/prof-correctness/pull/165), [#187](https://github.com/DataDog/prof-correctness/pull/187) | MERGED | CI | 3.14/3.15 images; #187 replayed #165 onto main | expected | merge-base guard |
| [prof-correctness#194](https://github.com/DataDog/prof-correctness/pull/194), [#203](https://github.com/DataDog/prof-correctness/pull/203), [#215](https://github.com/DataDog/prof-correctness/pull/215) | MERGED | CI | pinned wheels; uvloop sdist when cp315 wheel missing | expected | wheel-URL bump |
| [experimental#11316](https://github.com/DataDog/experimental/pull/11316)–[#11319](https://github.com/DataDog/experimental/pull/11319), [#11529](https://github.com/DataDog/experimental/pull/11529) | OPEN | staging harness | staging_ab deploy stack | expected | skill checklist |
| [experimental#11530](https://github.com/DataDog/experimental/pull/11530) | CLOSED | staging harness | collision guards; closed unmerged | expected | rebase onto stack |

**Automate for 3.16:** prof-correctness from day one; preflight auth/signing/wheels before blaming the profiler. mem_domain (#167) and alloc (#170) gates were drafted and never merged — track them explicitly.

---

## Repo-wide follow-up (aggregate)

Integration-bump PRs from the #17791 template (django, grpc, rq, …) are riot/lockfile follow-ups, not the profiling critical path.

---

## Dead-end lessons

| PR | Lesson |
| --- | --- |
| #17977, #17978 | Do not widen SSI / classifiers before natives and wrapping context are on main. Relanded via #19258 / #19843. |
| #18146 | Do not flip the full cp315 matrix green before the stack exists. Split allow_failure (#19270) then require wheels (#20450). |
| #19865 | Stock manylinux + metadata-only does not unblock Cython/bytecode pins. Mirror first, then Cython pin (#19861). |
| #17055, #17294, #17532, #17624, #19271 | Monoliths do not merge. Slice native → collectors → asyncio. |
| #18389, #18503, #18504, #17295, #17446 | Early monitoring / runbook / wrapping spikes were absorbed by #19269–#19273. |
| #20003 | `PY_UNWIND` is not a valid local monitoring event — check event IDs each beta. |
| images#11374 | Duplicate of #11355. |
| prof-correctness#165 → #187 | Wrong merge target; replay was required. |
| prof-correctness#167, #170 | mem_domain and alloc gates never merged. |
| #19254 | Mega "official support + SSI + reno" does not merge. |
