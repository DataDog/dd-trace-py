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

### Sequencing revision (approved 2025-11-05)

The original per-slice sequencing (extract one module, bridge it, then the
next) is revised for the shared-native cluster: the modules that sit on the
shared runtime (`http_client`, `remote_config`/`rc_shm`, `data_pipeline`,
`telemetry`) are extracted as **one campaign** into
`datadog-apm-python/packages/ddtrace-internal-native`, under **one freeze
window** and with **one bridge PR** for the whole cluster, because they are
mutually coupled (the writer's `TraceExporter` wires the telemetry handle;
remote-config drives `http_client`; `native_runtime` wraps `SharedRuntime`
for all of them). Rationale: a single freeze window avoids repeated
freeze/unfreeze cycles on `shared_runtime`, and moving the real
`shared_runtime` in the same campaign removes the temporary per-package
runtime duplication that a telemetry-only first slice would otherwise need.

The campaign keeps the per-slice guardrails (each added surface gets its own
gates and semver label; `data_pipeline`/TraceExporter is benchmarked before
the bridge switches the writer hot path). The compatibility window stays
**standard**: dd-trace-py keeps fallback copies after the shared package is
published, and the freeze ends at **bridge retirement** (deletion of the
dd-trace-py copies), not at publication. The first slice of the campaign
(telemetry worker + minimal runtime glue) is extracted and gated in
`datadog-apm-python` under this freeze (`package-freeze.toml`).

[Workflow](../../.agents/skills/package-split/SKILL.md) · [Commands/schemas](../../scripts/package_split/README.md)
