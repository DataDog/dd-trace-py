# Python 3.15 profiling PR catalog

**Vintage:** 2026-09-24 (author sweep `vlad-scherbich`, created ≥2026-03-01; plus `#17849` by P403n1x87).
**Scope:** profiling Continuous Profiler bring-up for CPython 3.15 only. Integration test-enable PRs (django, grpc, rq, …) are one aggregate row.
**Method:** direct `gh pr view` on known + cross-linked PRs (search API hit rate limits mid-sweep). Each row is in scope only if the diff touches profiling natives/collectors/`_asyncio.py`, wrapping/`sys.monitoring`, pyo3/Cython/riot/CI/wheels/images/SSI, or correctness/staging harness for 3.15.
**Status labels:** MERGED / OPEN / DRAFT / CLOSED (superseded).

Feeds: runbook expected/unexpected sections, `migrate-profiling-new-cpython` skill, version-registry scaffold candidates. The stacked `cpython_delta` pipeline (separate PR) uses native/layout rows as ground truth.

---

## Alpha — native ABI / build path

| PR | State | Category | Trigger | Expected? | Automate |
| --- | --- | --- | --- | --- | --- |
| [dd-trace-py#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | MERGED | native ABI | `PyFrameState` renumber; `FRAME_OWNED_BY_CSTACK` removed; layout contracts | expected | `cpython_delta` + skill checklist |
| [dd-trace-py#19270](https://github.com/DataDog/dd-trace-py/pull/19270) | MERGED | build/CI matrix | compile natives on 3.15; allow_failure job | expected | script/template (riot/CI) |
| [dd-trace-py#19268](https://github.com/DataDog/dd-trace-py/pull/19268) | CLOSED | test gating | native test install subdirs | superseded → absorbed | skill checklist |
| [dd-trace-py#19947](https://github.com/DataDog/dd-trace-py/pull/19947) | CLOSED | test gating | 3.15 gtest under `test/py315` | superseded | skill checklist |
| [dd-trace-py#19271](https://github.com/DataDog/dd-trace-py/pull/19271) | CLOSED | build/CI matrix | wire 3.15 into matrix/riot/CI early | superseded → #19270 | script/template |
| [libdatadog#1847](https://github.com/DataDog/libdatadog/pull/1847) | MERGED | lockfiles | pyo3 → 0.28 for 3.14/3.15 | expected | script/template (pin bump) |
| [libdatadog#2491](https://github.com/DataDog/libdatadog/pull/2491) | MERGED | lockfiles | pyo3 0.28 → 0.29 | expected | script/template |
| [dd-trace-py#18429](https://github.com/DataDog/dd-trace-py/pull/18429) | MERGED | packaging/SSI-adjacent | crashtracker/tracer_flare for pyo3 0.28 | expected | skill checklist |
| [dd-trace-py#19903](https://github.com/DataDog/dd-trace-py/pull/19903) | MERGED | native ABI | `PyContextVar_*` local decls (limited API) | surprise (limited-API) | skill checklist |

**Automate for 3.16:** run `find-cpython-usage` → `compare-cpython-versions` (and later `cpython_delta`) at first alpha; stub version-registry entry; open tracking issue from PEP phase.

---

## Beta — collectors, asyncio, wrapping

| PR | State | Category | Trigger | Expected? | Automate |
| --- | --- | --- | --- | --- | --- |
| [dd-trace-py#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | MERGED | asyncio hook | `wrap()` unavailable on 3.15 → `sys.monitoring` PY_RETURN | surprise (path) | skill checklist + human judgment |
| [dd-trace-py#19247](https://github.com/DataDog/dd-trace-py/pull/19247) | MERGED | wrapping | sys.monitoring multiplexer for 3.15 | expected | skill checklist |
| [dd-trace-py#19910](https://github.com/DataDog/dd-trace-py/pull/19910) | MERGED | wrapping | wrap trampoline on 3.15 (PROF-15852) | expected | skill checklist |
| [dd-trace-py#17849](https://github.com/DataDog/dd-trace-py/pull/17849) | MERGED | wrapping | wrapping context support (author P403n1x87) | expected | skill checklist |
| [dd-trace-py#19724](https://github.com/DataDog/dd-trace-py/pull/19724) | MERGED | packaging | don't crash apps on broken 3.15 imports | surprise | skill checklist (fail-soft) |
| [dd-trace-py#19267](https://github.com/DataDog/dd-trace-py/pull/19267) | MERGED | docs-adjacent | logging version warning on 3.15 | expected | script/template |

**Automate for 3.16:** probe `wrap()` vs `sys.monitoring` on first beta; keep dual-path gate in `_asyncio.py`; fail-closed compat script before claiming asyncio PASS.

---

## RC — images, wheels, engraver, Cython

| PR | State | Category | Trigger | Expected? | Automate |
| --- | --- | --- | --- | --- | --- |
| [images#9814](https://github.com/DataDog/images/pull/9814) | MERGED | wheels/images | mirror manylinux/musllinux 2026.05.13-1 (cp315) | expected | script/template (mirror) |
| [images#9815](https://github.com/DataDog/images/pull/9815) | MERGED | wheels/images | bump dd-trace-py base images | expected | script/template |
| [dd-trace-py#17959](https://github.com/DataDog/dd-trace-py/pull/17959) | MERGED | wheels/images | unblock cp315 wheels via base-image bump | expected | script/template |
| [images#11355](https://github.com/DataDog/images/pull/11355) | MERGED | wheels/images | mirror 2026.08.04-1 (cp315 rc1) | expected | script/template |
| [images#11356](https://github.com/DataDog/images/pull/11356) | MERGED | wheels/images | bump bases to 2026.08.04-1 (PROF-15844) | expected | script/template |
| [images#11374](https://github.com/DataDog/images/pull/11374) | CLOSED | wheels/images | duplicate rc1 mirror | superseded → #11355 | — |
| images#11732 | MERGED (git) | wheels/images | engraver `python:3.15.0rc2` (+fips) on Ubuntu 26.04 | expected | script/template (engraver) |
| [dd-trace-py#19936](https://github.com/DataDog/dd-trace-py/pull/19936) | MERGED | wheels/images | bump wheel-builder `IMAGE_TAG`s after images | expected | script/template |
| [dd-trace-py#19861](https://github.com/DataDog/dd-trace-py/pull/19861) | MERGED | build/CI matrix | Cython&lt;3.3 on 3.15; cp315 wheels optional | surprise (Cython) | version registry pin |
| [dd-trace-py#19880](https://github.com/DataDog/dd-trace-py/pull/19880) | MERGED | packaging/SSI | withhold cp315 from PyPI/prerelease index | expected | skill checklist |
| [dd-trace-py#20450](https://github.com/DataDog/dd-trace-py/pull/20450) | OPEN | wheels/images | require cp315 wheels; schedule lib_injection on 3.15 | expected | script/template |
| [dd-trace-py#18146](https://github.com/DataDog/dd-trace-py/pull/18146) | CLOSED | build/CI matrix | enable cp315 across wheel/riot/CI too early | dead-end | lesson below |
| [dd-trace-py#19865](https://github.com/DataDog/dd-trace-py/pull/19865) | CLOSED | wheels/images | unblock cp315 on stock manylinux (wrong image) | dead-end | lesson below |
| dd-source (branches `vlad/py315-engraver-base-images`, `vlad/fix-py315-whl-installer-host-pip`) | local/WIP | wheels/images | seed engraver digests; wire `dd_py_image`; host-pip whl_library fix | expected | script/template |

**Automate for 3.16:** within days of each RC tag — engraver `python/3.X.YrcN{,-fips}` → dd-source digests → language-tools seed → IMAGE_TAG bump. Pin hermetic interpreter to the **exact** prerelease (a2≠rc2 ABI).

---

## Final — SSI / OCI / packaging gates

| PR | State | Category | Trigger | Expected? | Automate |
| --- | --- | --- | --- | --- | --- |
| [dd-trace-py#17977](https://github.com/DataDog/dd-trace-py/pull/17977) | CLOSED | packaging/SSI | SSI auto-instrumentation for 3.15 | dead-end (too early) | lesson below |
| [dd-trace-py#19254](https://github.com/DataDog/dd-trace-py/pull/19254) | CLOSED | packaging/SSI | official support + SSI + profiling reno mega | dead-end / split | lesson below |
| [dd-trace-py#20474](https://github.com/DataDog/dd-trace-py/pull/20474) | OPEN | packaging | PEP 440 local versions in verify-package-version | surprise | script/template |

**Automate for 3.16:** keep SSI/OCI `when: never` until final; do not publish SSI from the wheel PR.

---

## Correctness + staging harness

| PR | State | Category | Trigger | Expected? | Automate |
| --- | --- | --- | --- | --- | --- |
| [dd-trace-py#19207](https://github.com/DataDog/dd-trace-py/pull/19207) | MERGED | correctness scenarios | block profiling PRs on failing prof-correctness for py-315 | expected | skill checklist |
| [prof-correctness#191](https://github.com/DataDog/prof-correctness/pull/191) | CLOSED | correctness scenarios | pin `prof-python-3.15` to `python:3.15.0rc1` | expected | script/template (image pin) |
| [experimental#11316](https://github.com/DataDog/experimental/pull/11316)–[#11319](https://github.com/DataDog/experimental/pull/11319), [#11529](https://github.com/DataDog/experimental/pull/11529)–[#11530](https://github.com/DataDog/experimental/pull/11530) | OPEN/CLOSED | staging harness | staging_ab deploy stack (1/6–6/6) | expected | skill checklist (preflight) |

**Automate for 3.16:** prof-correctness `python_*_3.X` jobs + staging smoke → ai_gateway A/B. Preflight auth/signing/wheel availability before blaming the profiler.

---

## Docs / tooling / ADR (this layer)

| PR | State | Category | Trigger | Expected? | Automate |
| --- | --- | --- | --- | --- | --- |
| [dd-trace-py#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | OPEN (this) | docs | runbook, registry, skill, fail-closed scripts | expected | — |
| [dd-trace-py#20478](https://github.com/DataDog/dd-trace-py/pull/20478) | OPEN | docs | ADR: profiling readiness for py-315 | expected | — |
| [dd-trace-py#17791](https://github.com/DataDog/dd-trace-py/pull/17791) | MERGED | docs | 3.15 integration-bump PR template | expected | generalize to 3.X template |

---

## Repo-wide follow-up (aggregate)

| PR set | State | Category | Notes |
| --- | --- | --- | --- |
| Integration test-enable (django, grpc, rq, …) | various | build/CI matrix | Out of profiling scope. Track as repo-wide CPython bump follow-up, not in this runbook's critical path. |

---

## Dead-end lessons (closed / superseded)

| PR | Lesson |
| --- | --- |
| #17977 | Do not enable SSI/OCI before CPython final. |
| #18146 | Do not flip the full wheel/riot/CI matrix green before natives + asyncio + images exist. |
| #19865 | Stock manylinux without the mirrored cp3XX interpreter will not unblock wheels — mirror first. |
| #19254 | Mega "official support + SSI + reno" PRs do not merge; split by layer. |
| #11374 / #19268 / #19271 / #19947 | Duplicate or early-stack PRs get absorbed; keep one live tip per layer. |

---

## Cross-links

- Runbook: `docs/contributing-profiling-new-cpython.rst`
- Analysis: `docs/cpython-diffs/analysis_314_to_315.md`
- Stack map: `scripts/py315-stack/PROFILING_STACK.md`
- ADR: [#20478](https://github.com/DataDog/dd-trace-py/pull/20478)
- Orchestrator skill: `.claude/skills/migrate-profiling-new-cpython/SKILL.md`
