# Python 3.15 profiling PR catalog

**Vintage:** 2026-09-30. **Scope:** profiling-only bring-up for CPython 3.15.
**Author filter:** `vlad-scherbich`, created ≥ 2026-03-01 (plus reference #17849).
**Location:** `docs/cpython-diffs/py315_pr_catalog.md` (feeds the profiling CPython-upgrade runbook).
**Companion JSON (local research artifact, not committed):** `/tmp/py315_pr_catalog_summary.json`.

## Method (verified)

- Author sweep: `gh search prs --author vlad-scherbich --created >=2026-03-01`, month-by-month (Mar–Sep 2026) to stay under the 100/page search cap.
- Repos via GitHub.com search: `dd-trace-py` (265), `prof-correctness` (35), `libdatadog` (4), `system-tests` (7; none profiling-315).
- Repos needing personal `gh` account (search fails under `vlad-scherbich_ddog`): `images`, `experimental`.
- Enterprise-only (`ddoghq/*`, not searchable from this host's default token for PR API): `images#11732` confirmed via local `images` git merge `1e5e2333f`; `dd-source#98654` confirmed via prior session + local branch `vlad/fix-py315-whl-installer-host-pip` (commits not on `origin/main` as of vintage).
- Relevance filter: read titles + `gh api .../pulls/N/files` (73 dd-trace-py candidates); keep if diff touches PY_VERSION_HEX/3.15/cp315, profiling native/collectors/`_asyncio`, wrapping/`sys.monitoring`, pyo3/Cython/CI/wheels/images/engraver/SSI, prof-correctness, or staging_ab.
- Cross-check: issue #17817 / parent #17809, runbook on `vlad/315-profiling-dev-tooling`, `PROFILING_STACK.md`, ADR #20478, analysis_314_to_315.md.
- Scope: profiling-only. Integration test-enable PRs → one aggregate row. Non-315 profiling (lock/MEM/heap/hooks) excluded.
- Exception reference row: #17849 by P403n1x87.

## Field legend

| Field | Meaning |
| --- | --- |
| kind | native ABI, struct layout, asyncio hook, wrapping, build/CI matrix, wheels/images, lockfiles, packaging/SSI, test gating, correctness scenarios, staging harness, docs, collectors |
| expected | expected / surprise / rework / dead_end / status / reference / aggregate |
| automate | `cpython_delta` predicted · `script_template` mechanical · `skill_checklist` agent checklist · `human` judgment · `none` |

## Totals

- **In-scope PR rows (individual):** 116
- **Plus aggregate row:** 1
- **Catalog rows total:** 117
- Confirmed: images#11732 ✓, dd-source#98654 ✓, reference #17849 ✓

## 1. Alpha — natives, layout contracts, gated CI

| PR | State | Kind | Trigger | Expected? | Automate | Note |
| --- | --- | --- | --- | --- | --- | --- |
| [#17055](https://github.com/DataDog/dd-trace-py/pull/17055) | closed/draft | native ABI | PyFrameState renumber + FRAME_OWNED_BY_CSTACK removal (a7 header diff) | dead_end | cpython_delta | First monolithic native attempt; superseded by split stack ending in #19269. |
| [#17294](https://github.com/DataDog/dd-trace-py/pull/17294) | closed/draft | native ABI | same ABI break; NO_MERGE exploration | dead_end | none | NO_MERGE exploration of 3.15 profiling; lessons folded into later stack. |
| [#17295](https://github.com/DataDog/dd-trace-py/pull/17295) | closed/draft | docs | need runbook during early bring-up | dead_end | skill_checklist | Early runbook draft; revived/superseded by #19273 tooling. |
| [#17532](https://github.com/DataDog/dd-trace-py/pull/17532) | closed/draft | native ABI | continued NO_MERGE native work | dead_end | none | NO_MERGE continuation; closed when stack was re-cut. |
| [#17596](https://github.com/DataDog/dd-trace-py/pull/17596) | closed/draft | build/CI matrix | riot lockfiles for 3.15 venvs | rework | script_template | Massive lockfile regen attempt; superseded by later gated riot PRs. |
| [#18503](https://github.com/DataDog/dd-trace-py/pull/18503) | closed/draft | native ABI | PyFrameState / Echion layout (stacked attempt) | dead_end | cpython_delta | Stacked native C++/Rust ABI PR; content landed via #19269. |
| [#18504](https://github.com/DataDog/dd-trace-py/pull/18504) | closed/draft | collectors | collector private-API guards for 3.15 | dead_end | cpython_delta | Collectors half of early stack; asyncio path later redone as #19272. |
| [#17624](https://github.com/DataDog/dd-trace-py/pull/17624) | closed/draft | build/CI matrix | wire 3.15 into matrix after natives | dead_end | script_template | Early CI wire-up; closed when #19270/#19271 re-cut the stack. |
| [#17710](https://github.com/DataDog/dd-trace-py/pull/17710) | merged | build/CI matrix | testrunner needs 3.15-dev interpreter image | expected | script_template | Prep testrunner for 3.15-dev + lockfile job memory bump. |
| [#17791](https://github.com/DataDog/dd-trace-py/pull/17791) | merged | docs | repo-wide 3.15 volunteer process | expected | script_template | python_315_bump PR template; generalize to 3.X for next minor. |
| [#18094](https://github.com/DataDog/dd-trace-py/pull/18094) | merged | packaging/SSI | requires-python upper bound hygiene while profiling unready | expected | script_template | Kept <3.15 until natives/wheels ready; later widened on other PRs. |
| [ld#1847](https://github.com/DataDog/libdatadog/pull/1847) | merged | lockfiles | pyo3 must support 3.15 limited API / native | expected | script_template | pyo3 → 0.28; pairs with dd-trace-py #18429. |
| [#18429](https://github.com/DataDog/dd-trace-py/pull/18429) | merged | lockfiles | pyo3 0.28 adapt for crashtracker/flare on 3.14/3.15 | expected | script_template | Untitled-in-keyword-search example from plan; native pyo3 adapt. |
| [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | merged | native ABI | PyFrameState renumber + FRAME_OWNED_BY_CSTACK + build gates | expected | cpython_delta | Canonical native ABI / Echion layout / cmake contracts. Ground truth for cpython_delta backtest. |
| [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) | merged/draft | build/CI matrix | compile natives on 3.15 with allow_failure job | expected | script_template | Armed (build): collectors compile + gated CI. |
| [#19271](https://github.com/DataDog/dd-trace-py/pull/19271) | closed/draft | build/CI matrix | broader matrix wire after #19270 | dead_end | script_template | Broader CI wire; closed/superseded by later required-wheel path. |
| [#19207](https://github.com/DataDog/dd-trace-py/pull/19207) | merged | test gating | need automatic correctness gate on profiling PRs | expected | skill_checklist | prof-correctness GHA gate on profiling PRs. |
| [#19947](https://github.com/DataDog/dd-trace-py/pull/19947) | closed/draft | test gating | gtest for 3.15 layout contracts | dead_end | script_template | gtest install path; closed — contracts live under #19269 tests. |

**Automate for 3.16:**
- Run `cpython_delta` inventory+diff on OLD..NEW; require layout contracts in test_frame_state_XXX.cpp
- Scaffold version registry via Q4 verify tooling (#19273; not this closeout)
- Add allow_failure → required CI job template for new cp3XX
- Bump pyo3 in libdatadog when limited-API / abi3 requires it

## 2. Beta — wrapping, sys.monitoring, asyncio hook, collectors

| PR | State | Kind | Trigger | Expected? | Automate | Note |
| --- | --- | --- | --- | --- | --- | --- |
| [#17446](https://github.com/DataDog/dd-trace-py/pull/17446) | closed/draft | wrapping | generator/coroutine wrapping breaks on 3.15 | dead_end | human | Early wrapping exploration; superseded by #17849 stack. |
| [#17531](https://github.com/DataDog/dd-trace-py/pull/17531) | closed/draft | wrapping | continued wrapping work | dead_end | human | Closed; content landed via #17849. |
| [#17730](https://github.com/DataDog/dd-trace-py/pull/17730) | closed/draft | asyncio hook | harden asyncio introspection vs future CPython | dead_end | cpython_delta | Hardening attempt; asyncio path later moved to sys.monitoring. |
| [#17847](https://github.com/DataDog/dd-trace-py/pull/17847) | closed/draft | wrapping | closure trampoline for wrap() | dead_end | human | Trampoline exploration; #19910 later enabled wrap trampoline on 3.15. |
| [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) | merged | wrapping | WrappingContext / inject_hook need 3.15 assemblies | reference | human | REFERENCE (author P403n1x87). Wrapping context support for 3.15; prerequisite for import survival + wrap trampoline. |
| [#17995](https://github.com/DataDog/dd-trace-py/pull/17995) | closed/draft | asyncio hook | drop bytecode-lib from _asyncio.py | dead_end | human | Partial asyncio refactor; full fix is #19272 sys.monitoring. |
| [#18389](https://github.com/DataDog/dd-trace-py/pull/18389) | closed/draft | asyncio hook | wrap() bytecode patching broken on 3.15 for create_task | dead_end | human | First sys.monitoring asyncio attempt; revived as #19256/#19272. |
| [#19247](https://github.com/DataDog/dd-trace-py/pull/19247) | merged | wrapping | shared sys.monitoring multiplexer needed before asyncio hooks | surprise | skill_checklist | UNTITLED gotcha: multiplexer required before profiling can register PY_RETURN. Expected once wrap failed; not predicted by header-only ABI diff. |
| [#19256](https://github.com/DataDog/dd-trace-py/pull/19256) | merged/draft | asyncio hook | asyncio create_task wrap broken on 3.15 | rework | human | Interim merged draft; final form is #19272. |
| [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | merged | asyncio hook | wrap() unavailable for create_task on 3.15+; sys.monitoring since 3.12 | surprise | skill_checklist | TOP GOTCHA: keep wrap() below 3.15; sys.monitoring PY_RETURN on 3.15+. Fail-closed gate needed fix. Backtest ground truth with #19269. |
| [#19724](https://github.com/DataDog/dd-trace-py/pull/19724) | merged | wrapping | ModuleWatchdog/WrappingContext raise at import on 3.15 before natives built | surprise | skill_checklist | Import degrade: don't crash apps when wrap/natives unavailable on 3.15. |
| [#19910](https://github.com/DataDog/dd-trace-py/pull/19910) | merged | wrapping | after #17849, wrap() still gated on NEXT_PY=3.15 so raised on 3.15 | surprise | script_template | Bump NEXT_PY to 3.16 so wrap trampoline runs on 3.15; profiling asyncio still uses monitoring. |
| [#19903](https://github.com/DataDog/dd-trace-py/pull/19903) | merged | native ABI | limited-API builds need local PyContextVar_* decls | surprise | script_template | Native limited-API declaration gap surfaced during 3.15 bring-up. |
| [#19911](https://github.com/DataDog/dd-trace-py/pull/19911) | merged | docs | unsupported-version errors hid which CPython was running | expected | script_template | Include running CPython version in unsupported error. |
| [#19928](https://github.com/DataDog/dd-trace-py/pull/19928) | merged | docs | scattered version checks; need MAX_PY/NEXT_MAX helpers | expected | script_template | Universal version-check helpers (compat). |
| [#20397](https://github.com/DataDog/dd-trace-py/pull/20397) | merged | docs | finish migration to version helpers | expected | script_template | Migrate remaining sys.version_info gates. |
| [#19601](https://github.com/DataDog/dd-trace-py/pull/19601) | open/draft | wrapping | enable multiplexer on 3.12+ (follow-up) | status | human | STATUS: broaden multiplexer beyond 3.15-only. |
| [#20003](https://github.com/DataDog/dd-trace-py/pull/20003) | closed/draft | asyncio hook | PY_UNWIND rejected as local event on some 3.15 builds | dead_end | skill_checklist | Local-event fallback; closed — may still be needed; track as lesson. |
| [#20005](https://github.com/DataDog/dd-trace-py/pull/20005) | closed/draft | collectors | optional Cython collectors ImportError on 3.15 | dead_end | skill_checklist | CollectorUnavailable stubs; closed without merge. |
| [#20006](https://github.com/DataDog/dd-trace-py/pull/20006) | closed/draft | asyncio hook | current_task() RuntimeError when no running loop | dead_end | skill_checklist | Narrow asyncio wrapper fix; closed. |
| [#20671](https://github.com/DataDog/dd-trace-py/pull/20671) | open/draft | collectors | 3.15 shutdown clears registry lock; memalloc bytearray domain change | status | human | STATUS: fix profiling::profile / profile-memalloc on 3.15. |

**Automate for 3.16:**
- Probe wrap() on create_task; if fail, register sys.monitoring on new minor only
- Ensure monitoring multiplexer exists before collector hooks
- Import-degrade path: ModuleWatchdog/WrappingContext must not crash apps
- Bump NEXT_PY gate so wrap trampoline runs on previous-new minor

## 3. RC — engraver images, digests, wheels, Cython, hermetic pin

| PR | State | Kind | Trigger | Expected? | Automate | Note |
| --- | --- | --- | --- | --- | --- | --- |
| images#9814 | merged | wheels/images | pypa manylinux must ship cp315 interpreter | expected | script_template | Mirror manylinux2014 + musllinux_1_2 2026.05.13-1 (cp315). |
| images#9815 | merged | wheels/images | dd-trace-py base image bump after mirror | expected | script_template | Register bumped manylinux/musllinux bases for dd-trace-py. |
| [#17959](https://github.com/DataDog/dd-trace-py/pull/17959) | merged | wheels/images | cp315 wheels blocked without new base IMAGE_TAGs | expected | script_template | Bump base images to 2026.05.13-1 inside dd-trace-py CI. |
| [#18146](https://github.com/DataDog/dd-trace-py/pull/18146) | closed/draft | build/CI matrix | enable cp315 across wheel matrix before natives ready | dead_end | script_template | DEAD-END LESSON: enabling full matrix before natives/packaging ready creates thrash; wait for #19269+#19861 path. |
| images#11355 | merged | wheels/images | rc1 manylinux must ship updated cp315 | expected | script_template | Mirror manylinux 2026.08.04-1 (cp315 rc1). |
| images#11356 | merged | wheels/images | consume rc1 manylinux mirror | expected | script_template | Bump dd-trace-py bases to 2026.08.04-1 (PROF-15844). |
| images#11374 | closed/draft | wheels/images | duplicate of #11355 | dead_end | none | Duplicate mirror PR; closed. |
| [#19936](https://github.com/DataDog/dd-trace-py/pull/19936) | merged | wheels/images | IMAGE_TAG bump after images#11356 | expected | script_template | Wheel-builder IMAGE_TAGs after images mirror. |
| [#20001](https://github.com/DataDog/dd-trace-py/pull/20001) | closed | wheels/images | unofficial-stack IMAGE_TAGs to rc1 | dead_end | script_template | Unofficial-stack IMAGE_TAG bump; closed. |
| [#19861](https://github.com/DataDog/dd-trace-py/pull/19861) | merged | lockfiles | Cython 3.3 breaks 3.15; wheels must start optional | surprise | script_template | TOP GOTCHA: pin Cython<3.3 on 3.15; cp315 REQUIRED_PLATFORMS empty (optional). |
| [#19865](https://github.com/DataDog/dd-trace-py/pull/19865) | closed/draft | wheels/images | attempt to unblock wheels on stock manylinux via requires-python only | dead_end | script_template | DEAD-END LESSON: widening requires-python alone is not enough; need Cython pin + optional platforms (#19861) then require later (#20450). |
| [#19880](https://github.com/DataDog/dd-trace-py/pull/19880) | merged | wheels/images | don't publish unready cp315 to PyPI/prerelease index | expected | script_template | Withhold cp315 from PyPI and prerelease index while optional. |
| [#19904](https://github.com/DataDog/dd-trace-py/pull/19904) | merged | build/CI matrix | riot must install editable package on 3.15 | expected | script_template | build_base_venvs: let riot install dev package on 3.15. |
| [#19905](https://github.com/DataDog/dd-trace-py/pull/19905) | closed/draft | build/CI matrix | testrunner 3.15-dev stale vs current CPython | dead_end | script_template | Superseded by #19907. |
| [#19906](https://github.com/DataDog/dd-trace-py/pull/19906) | merged | build/CI matrix | smoke_test must run on 3.15 | expected | script_template | Schedule smoke_test.py on 3.15. |
| [#19907](https://github.com/DataDog/dd-trace-py/pull/19907) | merged | build/CI matrix | testrunner cache + Cython pin for 3.15 | expected | script_template | Rebuild 3.15-dev; pin Cython<3.3 on 3.15 cache. |
| [#20157](https://github.com/DataDog/dd-trace-py/pull/20157) | merged | wheels/images | dependency-wheel download fails until python image mirrored | surprise | script_template | Skip 3.15 dependency-wheel download until image mirrored. |
| [images#11732](https://github.com/ddoghq/images/pull/11732) | merged | wheels/images | Rapid/staging needs engraver python:3.15.0rc2 (+fips) | expected | script_template | CONFIRMED (ddoghq/images; local git 1e5e2333f). First engraver packages for 3.15.0rc2 + fips on Ubuntu 26.04. Keyword search missed this. |
| [dd-source#98654](https://github.com/ddoghq/dd-source/pull/98654) | open/draft | wheels/images | whl_installer PYTHONPATH→vendored pip 24.0 breaks 3.15 locate_file; need engraver digests + dd_py_image | surprise | skill_checklist | CONFIRMED (ddoghq/dd-source). TOP GOTCHA: host-pip patch + seed engraver digests + wire dd_py_image. Also hermetic 3.15 must pin exact prerelease (a2≠rc2 ABI). Not on main as of vintage. Keyword search missed this. |
| [ld#2491](https://github.com/DataDog/libdatadog/pull/2491) | merged | lockfiles | pyo3 0.28→0.29 for continued 3.15 native | expected | script_template | Second pyo3 bump during bring-up. |
| [#20450](https://github.com/DataDog/dd-trace-py/pull/20450) | merged | wheels/images | promote cp315 from optional to required; schedule lib_injection | expected | script_template | Require cp315 wheels; schedule lib_injection on 3.15. |
| [#20474](https://github.com/DataDog/dd-trace-py/pull/20474) | open | packaging/SSI | hermetic pip check rejects PEP 440 local versions | surprise | script_template | STATUS/GOTCHA: accept PEP 440-equivalent local versions in verify-package-version. |
| [#19942](https://github.com/DataDog/dd-trace-py/pull/19942) | merged/draft | packaging/SSI | widen requires-python to include 3.15 | rework | script_template | Widened requires-python (draft merge); watch stacking with withhold/require. |
| [#19943](https://github.com/DataDog/dd-trace-py/pull/19943) | merged/draft | build/CI matrix | supported-versions generate job | rework | script_template | Add 3.15 to supported-versions generate; follow-ups #19995 open. |
| [#19995](https://github.com/DataDog/dd-trace-py/pull/19995) | open/draft | build/CI matrix | supported-versions generate still incomplete | status | script_template | STATUS: reopen/continue supported-versions 3.15. |
| [#19996](https://github.com/DataDog/dd-trace-py/pull/19996) | open/draft | packaging/SSI | requires-python widen follow-up | status | script_template | STATUS: widen requires-python follow-up draft. |

**Automate for 3.16:**
- Within days of each RC tag: engraver python/3.X.YrcN{,-fips} → digests → language-tools → IMAGE_TAG/manylinux mirror
- Pin hermetic interpreter to exact prerelease (never aN when building for rcM)
- Cython upper-bound pin template for new minor; start wheels optional then require
- Rapid bake: ensure whl_installer does not force vendored pip onto host PYTHONPATH

## 4. Final — SSI/OCI, classifiers, packaging, reno

| PR | State | Kind | Trigger | Expected? | Automate | Note |
| --- | --- | --- | --- | --- | --- | --- |
| [#17977](https://github.com/DataDog/dd-trace-py/pull/17977) | closed/draft | packaging/SSI | SSI allow-list for 3.15 auto-instrumentation | dead_end | skill_checklist | DEAD-END LESSON: do NOT publish SSI/OCI from the wheel PR / before final. SSI stays when:never until final. |
| [#17978](https://github.com/DataDog/dd-trace-py/pull/17978) | closed/draft | packaging/SSI | py315 classifier before ready | dead_end | script_template | Classifier too early; wait for wrapping (#17849) + required wheels. |
| [#19258](https://github.com/DataDog/dd-trace-py/pull/19258) | merged/draft | packaging/SSI | lib-injection SSI allow-list attempt | rework | script_template | Interim SSI; superseded by #19843 exclusive-max approach. |
| [#19274](https://github.com/DataDog/dd-trace-py/pull/19274) | closed/draft | packaging/SSI | SSI allow-list stack tip | dead_end | script_template | Closed; SSI exclusive-max landed via #19843. |
| [#19275](https://github.com/DataDog/dd-trace-py/pull/19275) | closed/draft | docs | customer reno for profiling 3.15 | dead_end | skill_checklist | Reno fragment; will ship with ADR/#20478 era. |
| [#19254](https://github.com/DataDog/dd-trace-py/pull/19254) | closed/draft | packaging/SSI | official support + SSI + reno stack tip | dead_end | skill_checklist | Monolithic tip closed; pieces split across #19843/#20450/#20478. |
| [#19843](https://github.com/DataDog/dd-trace-py/pull/19843) | merged | packaging/SSI | raise SSI exclusive max to 3.16; pre-stage 3.15 injection | expected | script_template | SSI exclusive max 3.16; pre-stage injection without publishing OCI early. |
| [#19267](https://github.com/DataDog/dd-trace-py/pull/19267) | merged | docs | logging version warning for unsupported | expected | script_template | Logging contrib version warning (adjacent packaging signal). |

**Automate for 3.16:**
- SSI/OCI stays when:never until final; exclusive-max bump only
- Classifier + reno + ADR after functional gates green
- Do not publish SSI from the wheel PR (#17977 lesson)

## 5. Validation — prof-correctness gates

| PR | State | Kind | Trigger | Expected? | Automate | Note |
| --- | --- | --- | --- | --- | --- | --- |
| [pc#165](https://github.com/DataDog/prof-correctness/pull/165) | merged | correctness scenarios | 314v315 correctness gate for profiling migration | expected | script_template | feat(ci): add 3.14/3.15 Docker images and scenario exclusions |
| [pc#166](https://github.com/DataDog/prof-correctness/pull/166) | merged | correctness scenarios | 314v315 correctness gate for profiling migration | expected | script_template | feat(python): add core 3.14/3.15 gate scenarios |
| [pc#168](https://github.com/DataDog/prof-correctness/pull/168) | merged | correctness scenarios | 314v315 correctness gate for profiling migration | expected | script_template | feat(python): add live_heap 3.14/3.15 gate scenarios |
| [pc#169](https://github.com/DataDog/prof-correctness/pull/169) | merged | correctness scenarios | 314v315 correctness gate for profiling migration | expected | script_template | feat(python): add cpu-time gate scenarios for 3.14/3.15 |
| [pc#171](https://github.com/DataDog/prof-correctness/pull/171) | merged | correctness scenarios | 314v315 correctness gate for profiling migration | expected | script_template | feat(python): add stack-advanced gate scenarios for 3.14/3.15 |
| [pc#172](https://github.com/DataDog/prof-correctness/pull/172) | merged | correctness scenarios | 314v315 correctness gate for profiling migration | expected | script_template | feat(python): add cross-cutting gate scenarios for 3.14/3.15 |
| [pc#174](https://github.com/DataDog/prof-correctness/pull/174) | merged | test gating | 314v315 correctness gate for profiling migration | expected | script_template | chore(ci): pin ruff and add pre-commit hooks |
| [pc#187](https://github.com/DataDog/prof-correctness/pull/187) | merged | correctness scenarios | 314v315 correctness gate for profiling migration | rework | script_template | feat(ci): add 3.14/3.15 Docker images and scenario exclusions (reland) |
| [pc#189](https://github.com/DataDog/prof-correctness/pull/189) | merged | correctness scenarios | 314v315 correctness gate for profiling migration | surprise | skill_checklist | feat(python): retune values and margins for 314 v 315 gates |
| [pc#190](https://github.com/DataDog/prof-correctness/pull/190) | merged | correctness scenarios | 314v315 correctness gate for profiling migration | surprise | skill_checklist | fix(python): use theoretical gate values instead of empirical |
| [pc#194](https://github.com/DataDog/prof-correctness/pull/194) | merged | test gating | 314v315 correctness gate for profiling migration | expected | script_template | ci: run 3.15 gates against a pinned S3 wheel |
| [pc#195](https://github.com/DataDog/prof-correctness/pull/195) | merged | test gating | 314v315 correctness gate for profiling migration | expected | script_template | ci: fail when 3.14 and 3.15 gate captures diverge |
| [pc#203](https://github.com/DataDog/prof-correctness/pull/203) | merged | test gating | 314v315 correctness gate for profiling migration | expected | script_template | chore(ci): validate ddtrace Python checks with 4.15.0rc2 wheel |
| [pc#211](https://github.com/DataDog/prof-correctness/pull/211) | merged | test gating | 314v315 correctness gate for profiling migration | expected | script_template | ci: add octo-sts policy for dd-trace-py GHA downstream trigger |
| [pc#213](https://github.com/DataDog/prof-correctness/pull/213) | merged | test gating | 314v315 correctness gate for profiling migration | expected | script_template | ci: drop extra GitLab claim_pattern from dd-trace-py trigger-ci |
| [pc#214](https://github.com/DataDog/prof-correctness/pull/214) | merged | correctness scenarios | 314v315 correctness gate for profiling migration | surprise | skill_checklist | fix(python_cpu): widen 2:1 share margin to ±10 |
| [pc#215](https://github.com/DataDog/prof-correctness/pull/215) | merged | correctness scenarios | 314v315 correctness gate for profiling migration | expected | script_template | ci: enable uvloop 3.15 checks via sdist |
| [pc#216](https://github.com/DataDog/prof-correctness/pull/216) | merged | test gating | 314v315 correctness gate for profiling migration | expected | script_template | ci: tighten 3.14 vs 3.15 compare gate to 3pp |
| [pc#217](https://github.com/DataDog/prof-correctness/pull/217) | merged | test gating | 314v315 correctness gate for profiling migration | expected | script_template | ci: print one-line reason when Compute matrix fails |
| [pc#221](https://github.com/DataDog/prof-correctness/pull/221) | merged | test gating | 314v315 correctness gate for profiling migration | expected | script_template | ci: optional ddtrace_install_url for downstream-python |
| [pc#161](https://github.com/DataDog/prof-correctness/pull/161) | closed/draft | correctness scenarios | 314v315 gate iteration | dead_end | skill_checklist | early draft scenarios |
| [pc#164](https://github.com/DataDog/prof-correctness/pull/164) | closed/draft | correctness scenarios | 314v315 gate iteration | dead_end | skill_checklist | superseded by #169 |
| [pc#167](https://github.com/DataDog/prof-correctness/pull/167) | closed/draft | correctness scenarios | 314v315 gate iteration | dead_end | skill_checklist | closed; not required for 315 functional claim |
| [pc#170](https://github.com/DataDog/prof-correctness/pull/170) | closed/draft | correctness scenarios | 314v315 gate iteration | dead_end | skill_checklist | closed draft |
| [pc#188](https://github.com/DataDog/prof-correctness/pull/188) | closed/draft | correctness scenarios | 314v315 gate iteration | dead_end | skill_checklist | docs closed |
| [pc#191](https://github.com/DataDog/prof-correctness/pull/191) | closed/draft | correctness scenarios | 314v315 gate iteration | dead_end | skill_checklist | DEAD-END: pin exact prerelease; rc1→rc2 needed (#202) |
| [pc#202](https://github.com/DataDog/prof-correctness/pull/202) | closed/draft | correctness scenarios | 314v315 gate iteration | dead_end | skill_checklist | closed after pin landed elsewhere / superseded |
| [pc#183](https://github.com/DataDog/prof-correctness/pull/183) | merged | test gating | CI runner flake on ddprof jobs | expected | human | stabilize ddprof_julia and ddprof_live_heap (adjacent CI). |
| [#20444](https://github.com/DataDog/dd-trace-py/pull/20444) | merged | test gating | S3 wheel poll timeout too short for prof-correctness | surprise | script_template | Raise S3 wheel poll timeout to 60m. |
| [#20585](https://github.com/DataDog/dd-trace-py/pull/20585) | open | test gating | poll earliest S3 install script | status | script_template | STATUS: poll earliest S3 install script for prof-correctness. |

**Automate for 3.16:**
- prof-correctness python_*_3.X jobs + 3.X-1 vs 3.X compare gate
- Pin gate image/wheel to exact prerelease; retune margins with theory not one-shot empiricism
- S3 wheel poll timeout / earliest-install-script knobs

## 6. Staging A/B harness (experimental)

| PR | State | Kind | Trigger | Expected? | Automate | Note |
| --- | --- | --- | --- | --- | --- | --- |
| [exp#11316](https://github.com/DataDog/experimental/pull/11316) | open/draft | staging harness | need staging A/B harness for profiling-315 validation | status | skill_checklist | staging_ab common helpers. Preflight: AppGate/vault, SSH unknown_key, passphrase in tmux, BUILD_WEDGED, wheel availability. |
| [exp#11317](https://github.com/DataDog/experimental/pull/11317) | open/draft | staging harness | need staging A/B harness for profiling-315 validation | status | skill_checklist | workers reuse run_wheels_pipeline. Preflight: AppGate/vault, SSH unknown_key, passphrase in tmux, BUILD_WEDGED, wheel availability. |
| [exp#11318](https://github.com/DataDog/experimental/pull/11318) | open/draft | staging harness | need staging A/B harness for profiling-315 validation | status | skill_checklist | services.cfg + dispatcher. Preflight: AppGate/vault, SSH unknown_key, passphrase in tmux, BUILD_WEDGED, wheel availability. |
| [exp#11319](https://github.com/DataDog/experimental/pull/11319) | open/draft | staging harness | need staging A/B harness for profiling-315 validation | status | skill_checklist | A/B runner on unified dispatcher. Preflight: AppGate/vault, SSH unknown_key, passphrase in tmux, BUILD_WEDGED, wheel availability. |
| [exp#11529](https://github.com/DataDog/experimental/pull/11529) | open | staging harness | need staging A/B harness for profiling-315 validation | status | skill_checklist | Rapid TD backend. Preflight: AppGate/vault, SSH unknown_key, passphrase in tmux, BUILD_WEDGED, wheel availability. |
| [exp#11530](https://github.com/DataDog/experimental/pull/11530) | closed | staging harness | need staging A/B harness for profiling-315 validation | dead_end | skill_checklist | DEAD-END LESSON: collision/integration-branch guards; auth/signing/BUILD_WEDGED are staging failures not profiler bugs. Preflight: AppGate/vault, SSH unknown_key, passphrase in tmux, BUILD_WEDGED, wheel availability. |

**Automate for 3.16:**
- Preflight auth (AppGate/vault), SSH keys, signing, wheel availability before blaming profiler
- smoke A/B then ai_gateway A/B; BUILD_WEDGED ≠ profiler bug
- Memory: expect ~+15% RSS; do not gate functional claim on parity

## 7. Tooling, runbook, ADR, delta pipeline

| PR | State | Kind | Trigger | Expected? | Automate | Note |
| --- | --- | --- | --- | --- | --- | --- |
| [#17792](https://github.com/DataDog/dd-trace-py/pull/17792) | closed | docs | repo-wide tracker doc | dead_end | none | Volunteer tracker doc; parent issue #17809 is source of truth. |
| [#19253](https://github.com/DataDog/dd-trace-py/pull/19253) | closed/draft | build/CI matrix | gated riot suites for 3.15 | dead_end | script_template | Gated suites draft; closed. |
| [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | open/draft | docs | need durable runbook/registry/skill for next minor | status | skill_checklist | STATUS: this PR — runbook, registry, skill, fail-closed scripts. Catalog feeds it. |
| [#20478](https://github.com/DataDog/dd-trace-py/pull/20478) | open/draft | docs | ADR: functional readiness; memory parity NOT claimed | status | skill_checklist | STATUS: ADR. Local AB ~+15% RSS (53% runtime / 47% profiler); don't gate on memory parity. |
| [#20565](https://github.com/DataDog/dd-trace-py/pull/20565) | open/draft | docs | automate CPython delta→worklist for 3.16 | status | cpython_delta | STATUS: cpython_delta inventory/diff pipeline (stacked on #19273). |
| [#20631](https://github.com/DataDog/dd-trace-py/pull/20631) | open/draft | docs | list CI suites still off 3.15 | status | script_template | STATUS: DO NOT MERGE inventory of suites still off 3.15. |
| [#20636](https://github.com/DataDog/dd-trace-py/pull/20636) | open/draft | build/CI matrix | PyPI lookup failure treated as missing coverage | status | script_template | STATUS: don't treat failed PyPI lookup as missing major coverage. |

**Automate for 3.16:**
- Copy runbook checklist; run migrate-profiling-new-cpython skill
- Update PROFILING_STACK.md status table
- Backtest cpython_delta on previous minor pair before trusting worklist

## 8. Repo-wide follow-up (not profiling-scoped)

| PR | State | Kind | Trigger | Expected? | Automate | Note |
| --- | --- | --- | --- | --- | --- | --- |
| *(aggregate)* | mixed | test gating | repo-wide integration parity on 3.15 (django/grpc/rq/…) | aggregate | script_template | AGGREGATE: integration test-enable PRs (e.g. #20002 crashtracker/grpc lockfiles, #20004 grpc schedule, #20007 crashtracker riot, #20627 schedule matrix for suites that pass, plus tracker #17809 volunteer rows). Out of profiling-only scope — one follow-up row for the repo-wide migration. |

**Automate for 3.16:**
- Use python_3X_bump template; schedule only suites that already pass

## Dead-end lessons (one-liners)

| PR | Lesson |
| --- | --- |
| #17977 | Do not enable SSI/OCI until final; never publish SSI from the wheel PR. |
| #18146 | Do not flip the full cp3XX matrix on before natives + Cython/optional-wheel path exist. |
| #19865 | Widening `requires-python` alone does not unblock wheels — need Cython pin + optional platforms, then require. |
| images#11374 | Duplicate mirror PRs waste review; one mirror + one consumer bump. |
| pc#191 | Pin gate images to the **exact** prerelease tag; a2≠rc2 ABI mismatch looks like profiler CrashLoop. |
| exp#11530 | Staging `BUILD_WEDGED` / auth / signing failures are not profiler bugs — preflight first. |
| #17055/#18503 stack | Prefer thin stacked PRs (native → compile gate → asyncio) over one monolithic ABI PR. |

## Top unexpected gotchas

1. **`wrap()` broke for asyncio `create_task` on 3.15** → `#19272` `sys.monitoring` on 3.15+ while keeping `wrap` below; wrong fail-closed gate had to be fixed. Multiplexer `#19247` was a prerequisite.
2. **Prerelease ABI mismatch (a2 vs rc2)** → CrashLoop / `_native` import errors that look like profiler bugs. Pin hermetic + prof-correctness images to the exact tag.
3. **Cython ≥3.3 on 3.15** → pin `<3.3`; start cp3XX wheels **optional** (`#19861`) then require (`#20450`).
4. **Rapid bake `whl_installer` PYTHONPATH** → vendored pip 24.0 lacks `locate_file` on 3.15 (`dd-source#98654`). Engraver `images#11732` digests required for true `@python_3_15_*` bases.
5. **Import-time crash via wrapping** before natives/wheels ready (`#19724`); **wrap trampoline still gated** after `#17849` until `#19910` bumped `NEXT_PY`.
6. **Staging failures are auth/signing/wheels** (`BUILD_WEDGED`, AppGate/vault, SSH `unknown_key`, passphrase in detached tmux) — not the profiler.
7. **Memory:** ~+15% RSS with profiler on (~53% runtime / ~47% profiler). memalloc is not the lever; do not gate the functional claim on memory parity.
8. **SSI/OCI early** (`#17977`) and **stock-manylinux-only unblock** (`#19865`) are dead ends.
9. **Hermetic pip / PEP 440 local versions** (`#20474`) break package verify during prerelease wheels.
10. **prof-correctness margins** needed theory-based retune (`pc#189/#190/#214`) and long S3 poll (`#20444`).

---
*Generated 2026-09-30. Profiling-only catalog for the CPython-upgrade runbook.*