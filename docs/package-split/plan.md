# Package split contract (proposal)

Approve names, boundaries, native replacements, compatibility windows and freeze activation; review the extraction POC first.
Destination: https://github.com/DataDog/datadog-apm-python/, a uv workspace (`packages/<name>/`, src layout, package-local build/tests). Its `ddtrace-internal==0.1.0` seed provides only `_ddtrace_internal`'s registry. Register `.importlinter` roots; follow `scripts/validate_packages.py`, `scripts/api_semver_check.py` and `release/<package>:<level>` labels.
Working graph: `ddtrace_internal` → `ddtrace_internal_base` (stdlib-only) + `ddtrace_internal_native` (compiled); no reverse edges/cycles. Coverage/products have separate contracts.

- Independent builds/tests/releases: no unrelated native rebuilds or coordinated consumer releases.
- New imports are inert, never import ddtrace; activation is explicit/idempotent/reversible. Preserve legacy behavior, canonical state and single instrumentation.
- Default Test Optimization is transitively native-free; optional native adapters never auto-activate. Approved pytest/IAST removals remain separate work.
- Declare narrow APIs/behavior/dependencies/lifecycle/ownership/rollback in `package-contract.toml`.
- Inventory → approve → negative fixtures → extract → exact standalone artifacts/coexistence/rollback → freeze → release rehearsal.
- Freeze pins source/build/test provenance; base-owned rules only, never execute PR code. Rewrites/exceptions need approval/sync records; exceptions expire.
- Missing prerequisites fail. Stop on unproven contracts, dependency closure, native policy, shared identity or parity; never auto-accept baselines or publish/cut over without approval.

[Workflow](../../.agents/skills/package-split/SKILL.md) · [Commands/schemas](../../scripts/package_split/README.md)
