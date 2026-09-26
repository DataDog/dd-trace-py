---
name: migrate-profiling-new-cpython
description: >
  Orchestrate Continuous Profiler support for a new CPython minor (e.g. 3.16).
  Detects PEP phase (alpha/beta/RC/final), runs the right sibling skills and
  scripts, and enforces hard stops from the py-315 lessons. Use when adding
  profiling support for a new Python version, starting a CPython upgrade for
  the profiler, or when asked to drive the 3.16 (or later) profiling migration.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - TodoWrite
  - WebSearch
---

# Migrate profiling to a new CPython minor

Orchestrator for **profiling only**. Detail lives in
`docs/contributing-profiling-new-cpython.rst`. Py-315 ground truth:
`docs/cpython-diffs/py315_pr_catalog.md`.

## Inputs

- **Target version** (required): e.g. `3.16`
- Optional: previous version (default = target − 0.1)

## Step 0 — Detect phase

1. Read local clock + [PEP release schedule](https://peps.python.org/) / CPython
   tags for the target (`v3.16.0aN`, `bN`, `rcN`, final).
2. Prefer skill `track-cpython-release-schedule` (user skills) when available.
3. Map phase → checklist rows that apply **now**:

| Phase | Apply |
| --- | --- |
| alpha | natives, layout contracts, gated CI job, version-registry scaffold |
| beta | collectors, asyncio hook probe, fail-closed compat script |
| RC | engraver within days of tag → digests → IMAGE_TAG → optional then required wheels |
| final | SSI/OCI, release note, ADR; flip `ssi_enabled` / `wheels_required` in registry |

Print which rows apply before doing work.

## Phase checklists (invoke siblings)

### Alpha

1. `python scripts/verify_profiler_compatibility.py --scaffold <TARGET>`
2. Skill **find-cpython-usage** (profiling file list below).
3. Skill **compare-cpython-versions** (prev → target tags). Hotspots:
   `tasks.h`, frame state / `pycore_frame*`, `_asyncio.py`.
4. Follow-up (stacked PR / later): `scripts/cpython_delta/` worklist when present.
5. Native ABI PR pattern: #19269. PASS: layout contract tests compile on target.

### Beta

1. Collectors + `setup.py` un-gate (pattern #19270).
2. Probe asyncio: does `wrap()` still work? If not, `sys.monitoring` path
   (pattern #19272). Keep `wrap` on older minors.
3. PASS: `python scripts/verify_profiler_compatibility.py --python <TARGET>`
   and `--compare` against baseline.

### RC

1. Engraver `python/<TARGET>rcN{,-fips}` in DataDog/images **within days** of the tag.
2. dd-source engraver digests + language-tools seed + whl_installer host-pip if needed.
3. Bump `IMAGE_TAG` / manylinux mirrors (pattern images#9814→#11356, dd-trace-py#19936).
4. Cython pin if needed (3.15: `<3.3`, #19861). Start wheels **optional**, then required (#20450).
5. **Pin hermetic interpreter to the exact prerelease** (a2 ≠ rc2 ABI).

### Final

1. SSI/OCI only now — never from the wheel PR (#17977 lesson).
2. Release note via **releasenote** skill.
3. ADR / readiness doc (pattern #20478).
4. Skill **run-tests** on profiling suites; `scripts/run-profiling-tests --python <TARGET>`.

## Validation gates (all phases that claim PASS)

| Gate | Command / artifact | PASS means |
| --- | --- | --- |
| Compat script | `verify_profiler_compatibility.py --python X [--compare]` | asyncio guards + samples |
| Local suites | `run-profiling-tests --python X` | profile + profile-memalloc riot |
| prof-correctness | `python_*_<X>` jobs | scenarios green on S3 wheel |
| Staging | experimental staging_ab smoke → ai_gateway | health + profile types; not memory parity |

## Known gotchas (symptom → cause)

| Symptom | Likely cause | Do not |
| --- | --- | --- |
| `_native` import error / CrashLoop after image bump | Prerelease ABI mismatch (wrong aN/rcN) | Blame profiler first |
| `wrap()` failures / missing asyncio task names on new minor | Need `sys.monitoring` path; or wrong fail-closed gate | Gate monitoring on NEXT_PY |
| Staging `BUILD_WEDGED`, AppGate/vault expiry, SSH `unknown_key`, passphrase hang in tmux | Auth / signing / wheel availability | Treat as profiler regression |
| RSS ~+15% with profiler on | Expected mix (~53% runtime / ~47% profiler); memalloc not the lever | Gate functional claim on memory parity |
| Wheel build fails on "stock" manylinux | Missing mirrored cp3XX (#19865) | Re-open early matrix PRs |
| Hermetic pip / local version rejects | PEP 440 local labels (#20474) | Skip package verify |

## Hard stops

- **Never** enable SSI/OCI before CPython final.
- **Never** claim memory parity without a real A/B; do not block functional PASS on RSS.
- **Never** skip staging preflight (auth, signing, wheel present).
- **Never** skip version-registry + baselines when adding a `PY_VERSION_HEX` guard.
- Profiling-only: integration test-enable is repo-wide follow-up.

## Profiling file list (for find-cpython-usage)

```
ddtrace/internal/datadog/profiling/
ddtrace/profiling/
ddtrace/profiling/_asyncio.py
ddtrace/internal/datadog/profiling/stack/echion/echion/cpython/tasks.h
ddtrace/internal/datadog/profiling/stack/src/echion/frame.cc
ddtrace/profiling/collector/_memalloc_tb.cpp
setup.py
scripts/profiles/profiling_versions.json
scripts/profiles/compatibility_baselines.json
```

## Related

- Runbook: `docs/contributing-profiling-new-cpython.rst`
- Catalog: `docs/cpython-diffs/py315_pr_catalog.md`
- Stack map: `scripts/py315-stack/PROFILING_STACK.md`
- Skills: `find-cpython-usage`, `compare-cpython-versions`, `run-tests`, `releasenote`
- Rule: `.cursor/rules/profiling-new-cpython.mdc`
