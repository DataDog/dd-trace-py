---

name: dependency-direction-analysis
description: >
  Run the dependency direction detector against ddtrace and propose architectural
  fixes for any violations found. Use this when adding or refactoring modules
  under ddtrace/internal, ddtrace/contrib, or any product package, or when the
  detect_layering_violations CI job reports new violations on a PR.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - Edit
  - TodoWrite
---

# Dependency direction

`scripts/import-analysis/layers.json` is the authority for zones, foundation modules, and
intentional exceptions. Foundation/contrib imports into products and imports between products
are restricted; the configured contrib-to-tracing exception is intentional.

```bash
uv run --script scripts/import-analysis/layers.py analyze <output.json>
uv run --script scripts/import-analysis/layers.py compare <base.json> <candidate.json>
```

Generate each snapshot at its respective revision and keep outputs in a temporary location.
The result lists `violations` and `uncovered` top-level modules. Read the detector's current
comparison rules when diagnosing CI; severity scores prioritize investigation, not correctness.

## Resolve the ownership problem

- Trace the names crossing the boundary. Move misplaced code to its owner, extract a genuinely
  shared contract, or use core events / registration to keep the dependency pointing downward.
- Security products subscribe to contrib events; see `.cursor/rules/isolated-responsibility.mdc`.
- Classify new top-level modules in `layers.json`: product code belongs in its product zone;
  shared foundation code must be explicitly listed. Product-owned `internal` packages need
  the corresponding zone mapping rather than falling into generic internal-core.
- Do not move code into an unclassified module merely to evade the check. Do not hide violations
  using deferred imports. Use `circular-import-analysis` for cycles.
- Add a zone exception only for a deliberate, durable dependency. Explain the architectural
  reason; ask when the intended ownership cannot be established from the task and repository.

Rerun both analysis and base comparison to confirm that the intended violation is resolved and
no new violations or uncovered modules were introduced.
