# Package split contract (proposal)

Approve names, boundaries, native replacements, compatibility windows and freeze activation; review the extraction POC first.
Destination: https://github.com/DataDog/datadog-apm-python/, a uv workspace (`packages/<name>/`, src layout, package-local build/tests). Its `ddtrace-internal==0.1.0` seed provides only `_ddtrace_internal`'s registry. Register `.importlinter` roots; follow `scripts/validate_packages.py`, `scripts/api_semver_check.py` and `release/<package>:<level>` labels.
Working graph: `ddtrace_internal` → `ddtrace_internal_base` (stdlib-only) + `ddtrace_internal_native` (compiled); no reverse edges/cycles. Coverage/products have separate contracts.

- Independent builds/tests/releases: no unrelated native rebuilds or coordinated consumer releases.
- New imports are inert, never import ddtrace; activation is explicit/idempotent/reversible. Preserve legacy behavior, canonical state and single instrumentation.
- Test Optimization may depend on native telemetry and writer components. A pure-Python package must not itself build compiled artifacts; that constraint does not extend to its dependencies. Approved pytest/IAST removals remain separate work.
- Review narrow APIs, behavior, lifecycle, ownership and rollback for each extraction. Keep executable build/runtime metadata in the destination's pyproject; the source harness requires no duplicate package contract.
- Inventory → approve → **freeze on main before any code moves** → negative fixtures → extract → exact standalone artifacts/coexistence/rollback → release rehearsal. Add file content hashes to main's existing frozen-paths.txt before destination work starts. Its Freeze check workflow is the single source freeze mechanism.
- The Freeze check validates the PR merge result against both base and merge-result hashes. Main must require the check and up-to-date branches. Rewrites require a separate PR removing the freeze entry while leaving the file unchanged; coordinate subsequent source changes with the extracted copy and freeze the replacement when needed. No TOML freeze manifest or path-scoped rewrite approval is used.
- Missing prerequisites fail. Stop on unproven contracts, dependency closure, native policy, shared identity or parity; never auto-accept baselines or publish/cut over without approval.
- The source CLI owns consumer inventories. Reuse main's freeze workflow, existing source import/wheel CI and destination package checks; encode compatibility and bridge behavior in concrete consumer tests through scripts/run-tests. The CLI reference maps retired generic gates to their coverage owners and remaining requirements.

[Workflow](../../.agents/skills/package-split/SKILL.md) · [Commands/schemas](../../scripts/package_split/README.md)
