---
name: package-split
description: Inventory consumers, enforce source freezes, and validate package extractions.
---
# Package split

Read [the plan](../../../docs/package-split/plan.md) and [CLI reference](../../../scripts/package_split/README.md); follow AGENTS.md.

1. Inventory `api-usage --module <target>` (production, then tests) and `api-surface`; review reported skipped files and non-Python references. Include source paths for re-export resolution; use `--allow-absent-target` for a consumer-only scan. Propose a narrow contract and obtain scope/name approval.
2. Freeze on main before any code moves: land the freeze manifest (package-freeze.toml) and the PR freeze guard on main as the entry point of the extraction. Approved rewrites pin their exact content (post_hash) and carry sync records; absorb upstream changes to frozen paths into the extracted copy in the same window.
3. Establish package-owned checks and negative fixtures at the destination before moving code. Reuse its build, import/dependency policy and lifecycle checks; do not port the source harness or duplicate package metadata.
4. Extract incrementally; bridges fall back only for an absent provider, never a broken installation. Preserve canonical identity and record bridge ownership/removal conditions.
5. Run destination package checks and source consumer suites against exact artifacts. Use scripts/run-tests for released/candidate combinations and bridge scenarios: assert canonical identity with `is`, missing versus broken providers, single instrumentation, install order and rollback. Add relevant sdist/resource/native-closure checks where no existing check establishes the promise; never weaken baselines or claim unrun checks passed.
6. Report changed boundaries, commands/results/evidence, blockers, compatibility/freeze status and rollback. No publishing or irreversible cutover without approval.
