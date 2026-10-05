---
name: migrate-profiling-new-cpython
description: >
  Pointers for Continuous Profiler support on a new CPython minor. Read the
  catalog and short runbook; do not invent a parallel checklist. Use when
  adding profiling support for a new Python version.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - WebSearch
---

# Migrate profiling to a new CPython minor

**Pointer skill** — read these, then act. This is not an orchestrator for 3.16
automation or agentic migration readiness.

| Artifact | Path |
| --- | --- |
| Short runbook (phase table + pointers) | `docs/contributing-profiling-new-cpython.rst` |
| Latest catalog (version answers) | `docs/cpython-diffs/py315_pr_catalog.md` |
| Header-diff notes | `docs/cpython-diffs/analysis_314_to_315.md` |
| Live stack / PR map | `scripts/py315-stack/PROFILING_STACK.md` |
| Version registry | `scripts/profiles/profiling_versions.json` |
| Sample baselines | `scripts/profiles/compatibility_baselines.json` |
| Light guardrails | `.cursor/rules/profiling-new-cpython.mdc` |

## Short runbook

1. Read the catalog for the previous completed minor (gotchas, hook path, PR map).
2. Classify phase from the runbook PEP table (alpha / beta / RC / final).
3. If scaffolding a new registry entry is useful:

   ```bash
   python scripts/verify_profiler_compatibility.py --scaffold X.Y
   ```

   Commit stubs only when the bring-up PR intends to land them.
   Keep `default_python` aligned with a suitespec-backed profiling minor.
4. For header inventory / diffs: `find-cpython-usage`, then `compare-cpython-versions`.
5. Full process depth, staging A/B playbooks, and `cpython_delta` are follow-ups —
   do not invent them here.

## Related skills

- `find-cpython-usage` — inventory profiling CPython dependencies
- `compare-cpython-versions` — diff CPython OLD→NEW
- `run-tests` — suitespec / suite execution
- `releasenote` — customer-facing reno at final
