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
intentional exceptions. `internal-core`/`contrib` imports into products and imports between
products are restricted; the configured contrib-to-tracing exception is intentional.
Explicitly listed foundation modules are exempt from these dependency-direction rules.

```bash
uv run --script scripts/import-analysis/layers.py analyze --root <candidate-checkout>/ddtrace <candidate.json>
uv run --script scripts/import-analysis/layers.py analyze --root <merge-base-checkout>/ddtrace <base.json>
uv run --script scripts/import-analysis/layers.py compare <base.json> <candidate.json>
```

Run both analyses with the candidate's detector and adjacent `layers.json`, matching CI;
only the source tree passed through `--root` changes. Keep outputs in a temporary location.
The result lists `violations` and `uncovered` top-level modules. Read the detector's current
comparison rules when diagnosing CI; severity scores prioritize investigation, not correctness.

For each violation, `from`/`to` identify the importing/imported modules and `from_zone`/`to_zone`
show their classified owners. `in_tangle` means the target participates in a circular-import
component; it does not by itself prove this particular edge creates the cycle.

`score` combines the rule's weight, how many modules depend on the target, and a cycle bonus.
Within the task's scope, investigate high scores and tangled targets first; read the current
formula in `layers.py`. A lower score does not make a forbidden dependency acceptable.

`uncovered` identifies top-level modules missing a zone or foundation classification. Resolve
that configuration gap explicitly; new uncovered modules fail the comparison independently of
violation scores. The comparison also distinguishes new edges from worsened existing edges.

## Resolve the ownership problem

- Trace the names crossing the boundary. Move misplaced code to its owner, extract a genuinely
  shared contract, or use core events / registration to keep the dependency pointing downward.
- Security products subscribe to contrib events; see `.cursor/rules/isolated-responsibility.mdc`.
- When shared machinery needs product behavior, define a narrow `Protocol` or callback contract
  at the shared boundary and let the product supply its implementation through the existing
  lifecycle. `Product` in `ddtrace/internal/products.py` is an example of a lifecycle contract.
- For types/constants needed by multiple products, extract only the common contract into a
  classified shared module. Keep product implementation imports out of that module, including
  imports through convenience re-exports that would recreate the dependency.
- Move product-specific behavior into its owning product. Move a utility into shared code only
  when it is genuinely product-independent; a helper's current location alone does not decide ownership.
- Do not relocate public product contracts such as `Tracer` or `Span` into shared code just to
  satisfy the detector. They are exported by `ddtrace/trace/__init__.py`; preserve that API and
  product ownership, using a narrower interface or event for other products' needs.
- Classify new top-level modules in `layers.json`: product code belongs in its product zone;
  shared foundation code must be explicitly listed. Product-owned `internal` packages need
  the corresponding zone mapping rather than falling into generic internal-core.
- Do not move code into an unclassified module merely to evade the check. Do not hide violations
  using deferred imports. Use `circular-import-analysis` for cycles.
- Propose a zone exception only for a deliberate, durable dependency, with the architectural
  reason explicit in the PR for human review. Do not decide unilaterally that an exception is
  acceptable merely to make the check pass.

Rerun both analysis and base comparison to confirm that the intended violation is resolved and
no new violations or uncovered modules were introduced.
