---
name: migrate-profiling-new-cpython
description: >
  Orchestrate Continuous Profiler support for a new CPython minor (e.g. 3.16).
  Detects PEP phase, scaffolds the version registry, and drives the runbook
  checklist. Use when adding profiling support for a new Python version.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - WebSearch
  - TodoWrite
---

# Migrate profiling to a new CPython minor

Agent-facing orchestrator. **Detail lives in the runbook — do not copy it here.**

- Runbook: `docs/contributing-profiling-new-cpython.rst`
- Live PR states: `scripts/py315-stack/PROFILING_STACK.md`
- Version registry: `scripts/profiles/profiling_versions.json`
- Sample baselines: `scripts/profiles/compatibility_baselines.json`

## Inputs

- Target minor: e.g. `3.16` (MAJOR.MINOR only).
- Optional: previous minor (default: target − 1 minor).

## Step 0 — Detect phase and scaffold

1. Read the PEP release schedule for the target minor. Compare against the local clock (verify TZ via system date).
2. Classify phase using the runbook table: alpha / beta / RC / final.
3. If `scripts/profiles/profiling_versions.json` has no entry for the target:

   ```bash
   python scripts/verify_profiler_compatibility.py --scaffold 3.16
   ```

   That writes registry + baseline stubs and prints open checklist rows. Do not
   commit the stubs unless the bring-up PR intends to land them.

4. Follow the runbook for that phase: playbooks, automation checklist, hard stops.

## Related skills

- `find-cpython-usage` — inventory profiling CPython dependencies
- `compare-cpython-versions` — diff CPython OLD→NEW
- `run-tests` — riot / suite execution
- `releasenote` — customer-facing reno at final
