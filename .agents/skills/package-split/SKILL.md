---
name: package-split
description: Inventory the APIs other modules consume, to choose package-split extraction boundaries.
---
# Package split

Run from the repository root; `--help` lists options. Both commands are static, over-approximating
inventories, not compatibility proofs, and always exit 0 on success (2 on usage or input errors).
Files the interpreter cannot parse are skipped and listed on stderr and in the JSON `unparsed` field.

```bash
scripts/package-split api-usage --module ddtrace.internal.coverage [--source-path ddtrace ...]
scripts/package-split api-surface [--source-path ddtrace ...] [--zones <layers.json>] [--top 30]
```

- `api-usage` lists every symbol of the target consumed outside it, with call/subclass/raise/except/read context.
  Run it over production code first, then use `--source-path ddtrace --source-path tests` for a combined inventory.
  For a test-only scan, use `--source-path tests --allow-absent-target`; re-export attribution then depends on facades present in that scope.
- `api-surface` groups consumed symbols by owning module and zone (default:
  `scripts/import-analysis/layers.json`), and suggests modules whose consumers all live in one zone.
- JSON evidence is written under `.package-split/<command>/<scope>/`; `api-usage` also nests it under the target module. The command prints the exact path (`--output` changes the root).

Use the results to propose a narrow supported API for the extraction and get scope and naming
approved before moving code. Report the commands run, the proposed surface, and usages you chose
not to support. Add fixtures for new analysis cases in `tests/package_split/`, then use
`scripts/run-tests` and `scripts/lint`.

## Extraction workflow

Read [the plan](../../../docs/package-split/plan.md) and [CLI reference](../../../scripts/package_split/README.md) before extracting code.

1. Inventory `api-usage --module <target>` (production, then tests) and `api-surface`; review reported skipped files and non-Python references. Include source paths for re-export resolution; use `--allow-absent-target` for a consumer-only scan. Propose a narrow contract and obtain scope/name approval.
2. Freeze on main before any code moves: add path=SHA-256 entries to the existing frozen-paths.txt, enforced by .github/workflows/freeze-check.yml. Require the Freeze check status and up-to-date branches. To rewrite a frozen file, first land a separate PR removing its entry without changing the file; synchronize subsequent changes with the extracted copy and refreeze when needed. Do not introduce another freeze mechanism.
3. Establish package-owned checks and negative fixtures at the destination before moving code. Reuse its build, import/dependency policy and lifecycle checks; do not port the source harness or duplicate package metadata.
4. Extract incrementally; bridges fall back only for an absent provider, never a broken installation. Preserve canonical identity and record bridge ownership/removal conditions.
5. Run destination package checks and source consumer suites against exact artifacts. Use scripts/run-tests for released/candidate combinations and bridge scenarios: assert canonical identity with `is`, missing versus broken providers, single instrumentation, install order and rollback. Add tests for sdist parity, resources and build artifacts where no existing check establishes the promise. Pure-Python build requirements apply to the package itself; native telemetry and writer dependencies are allowed. Never weaken baselines or claim unrun checks passed.
6. Report changed boundaries, commands/results/evidence, blockers, compatibility/freeze status and rollback. No publishing or irreversible cutover without approval.
