---
name: migrate-profiling-new-cpython
description: >
  Orchestrate Continuous Profiler support for a new CPython minor (e.g. 3.16).
  Detects PEP phase, runs sibling skills and gates, surfaces py-315 gotchas, and
  hard-stops unsafe claims. Use when adding profiling support for a new Python
  version, scaffolding the version registry, or driving the 3.X bring-up checklist.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - WebSearch
  - TodoWrite
---

# Migrate profiling to a new CPython minor

Agent-facing orchestrator for profiling-only CPython bring-up. **Point at the
runbook for detail — do not duplicate it here.**

- Runbook: `docs/contributing-profiling-new-cpython.rst`
- Stack status: `scripts/py315-stack/PROFILING_STACK.md`
- Version registry: `scripts/profiles/profiling_versions.json`
- Sample baselines: `scripts/profiles/compatibility_baselines.json`

## Inputs

- Target minor: e.g. `3.16` (MAJOR.MINOR only).
- Optional: previous minor (default: target − 1 minor).

## Step 0 — Detect phase

1. Read the PEP release schedule for the target minor (or CPython release calendar).
2. Compare against the local clock (verify TZ via system date — do not guess).
3. Classify phase:
   - **alpha** — natives + gated CI
   - **beta** — collectors + asyncio hook path
   - **RC** — engraver images within days, digests, optional→required wheels
   - **final** — SSI/OCI, classifiers, reno, ADR if claiming support
4. Load `scripts/profiles/profiling_versions.json`. If the target is missing:

   ```bash
   python scripts/verify_profiler_compatibility.py --scaffold 3.16
   ```

   That stubs the registry + baseline entry and **prints open checklist rows**.
   Only the rows for the current phase (and earlier unfinished ones) apply now.

## Phase playbooks

Each phase: invoke the sibling skill, run the script, require the PASS gate.

### Alpha

| Order | Sibling / script | PASS gate |
| --- | --- | --- |
| 1 | `find-cpython-usage` | Inventory of profiling CPython headers/structs/fields |
| 2 | `compare-cpython-versions` | Documented ABI/layout deltas for OLD→NEW |
| 3 | Native ABI + layout contracts (see runbook) | cmake / contract tests green on target |
| 4 | `scripts/run-profiling-tests --python X.Y --check-only` (or `--quick`) | verify script PASS |
| 5 | prof-correctness `python_*_X.Y` jobs | gate green or explicitly allow_failure until armed |

### Beta

| Order | Sibling / script | PASS gate |
| --- | --- | --- |
| 1 | Probe `wrap()` on `asyncio.create_task` | If broken → `sys.monitoring` on **new minor only**; keep wrap below |
| 2 | Collectors / `setup.py` un-gate + riot matrix | profile + profile-memalloc riot green |
| 3 | `scripts/run-profiling-tests --python X.Y` | verify `--compare` + riot PASS |

### RC

| Order | Sibling / script | PASS gate |
| --- | --- | --- |
| 1 | Engraver `python/X.Y.ZrcN{,-fips}` within days of RC | image tags exist |
| 2 | dd-source engraver digests → language-tools seed → whl_installer | digests + locate_file work |
| 3 | Wheels optional first, then required | REQUIRED_PLATFORMS empty → then require |
| 4 | Pin hermetic interpreter to **exact** prerelease tag | aN ≠ rcM ABI |
| 5 | Cython upper-bound pin if the new minor breaks | builds succeed |

### Final

| Order | Sibling / script | PASS gate |
| --- | --- | --- |
| 1 | `releasenote` skill | customer-facing reno (or explicit no-changelog) |
| 2 | SSI/OCI enable | **only** at final — see Hard stops |
| 3 | ADR / readiness doc if claiming support | linked from runbook |

## Known gotchas (symptom → cause)

| Symptom | Likely cause | Do this |
| --- | --- | --- |
| `_native` import error / CrashLoop right after an image bump | Prerelease ABI mismatch (built on aN, ran on rcM) | Pin hermetic interpreter to the **exact** prerelease tag |
| `wrap()` / create_task attribution broken on new minor | Bytecode patching unavailable; need monitoring path | Keep wrap below the new minor; `sys.monitoring` PY_RETURN on new minor+ |
| Staging `BUILD_WEDGED`, SSH `unknown_key`, hung passphrase in tmux | Auth / signing / AppGate / vault — **not** the profiler | Preflight auth before blaming samples |
| Staging failure "no wheel" | Wheel not published / wrong tag | Check REQUIRED_PLATFORMS + S3 wheel availability |
| Cython / cp3XX build explosions early in RC | Cython 3.3+ or required wheels too early | Pin Cython; start wheels **optional**, require later |
| Memory parity fails vs previous minor | ~+15% RSS with profiler on is expected; memalloc is not the lever | Do **not** gate the functional claim on memory parity |
| Hermetic pip / local version rejects | PEP 440 local-version check too strict | See runbook + #20474 lesson |

## Hard stops

- **Never** enable SSI/OCI before final. Do not publish SSI from the wheel PR.
- **Never** claim memory parity without a real A/B; do not block functional sign-off on RSS alone.
- **Never** skip staging/auth preflight and blame the profiler for `BUILD_WEDGED` / `unknown_key`.
- **Never** mix prerelease ABIs (aN wheel on rcM runtime).
- **Never** add a new `PY_VERSION_HEX` / version guard without updating the version registry and baselines (see `.cursor/rules/profiling-new-cpython.mdc`).

## Related skills

- `find-cpython-usage` — inventory profiling CPython dependencies
- `compare-cpython-versions` — diff CPython OLD→NEW (release-schedule notes may live on the ADR PR)
- `run-tests` — riot / suite execution
- `releasenote` — customer-facing reno at final
