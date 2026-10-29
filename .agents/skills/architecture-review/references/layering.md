# Layering and imports

ddtrace is split into zones: shared internal code (`internal-core`), integrations (`contrib`)
and products (tracing, appsec, IAST, profiling, debugging, LLMObs, ...).
`scripts/import-analysis/layers.json` defines the zones and the allowed exceptions. The CI jobs
`detect_layering_violations` and `detect_circular_imports` compare a PR against its merge base.
The `dependency-direction-analysis` and `circular-import-analysis` skills explain how to run
them locally and how to fix what they report. This file lists the rules a change must follow,
including the ones the detectors cannot see.

Rules marked **target** describe the agreed design for new code. Some existing code predates
them; do not flag unchanged code, but do not let a change extend a known exception.

## Trigger symbols

Apply this checklist when a diff:

- adds, moves or renames a module or package under `ddtrace/`
- adds an import that crosses a top-level package boundary (for example
  `ddtrace/internal/** -> ddtrace/appsec/**`, `ddtrace/contrib/** -> ddtrace/llmobs/**`, or
  `ddtrace/debugging/** -> ddtrace/profiling/**`)
- adds a function-local import or an `if TYPE_CHECKING:` import
- changes `scripts/import-analysis/layers.json`
- adds a product name, product attribute or product setting inside `ddtrace/internal/`,
  `ddtrace/_trace/` or `ddtrace/contrib/`

## Stop conditions

Any of these blocks the change:

1. A new violation or a new uncovered module reported by `layers.py compare` (LA-1, LA-7).
2. A new cycle reported by `cycles.py compare` (LA-2).
3. A function-local or `TYPE_CHECKING` import added to avoid a cycle or a layering violation
   (LA-3).
4. A new exception in `layers.json` without an architectural justification for human review
   (LA-7).

## Rules

### LA-1 Dependencies point from products to shared code

- `internal-core` and `contrib` do not import products.
- Products do not import other products. The exceptions are listed in `layers.json`
  (currently: contrib and CI Visibility may use tracing).
- Modules not covered by any zone are foundation code, exempt from both rules. That exemption
  is not a place to move code to so that the detector stops reporting it.

When shared code needs product behavior, invert the dependency: the shared side defines a
narrow `Protocol`, callback or core event, and the product registers its implementation from
its own lifecycle (see `Product` in `ddtrace/internal/products.py`).

### LA-2 No new import cycles

Fix a cycle by moving code to its owner, extracting the shared contract into a module that
imports neither side, or registering at startup. See `circular-import-analysis` for the
patterns and examples.

### LA-3 Do not hide dependencies

A function-local import or a `TYPE_CHECKING` guard that exists to avoid a cycle or a layering
violation hides the dependency without removing it. Deliberate lazy imports are fine when they
exist for startup cost or gevent compatibility. They must say so in a comment, and they must
not cross a forbidden zone boundary.

### LA-4 Shared infrastructure knows nothing about its consumers

Generic code used by several products (module watchdogs, code collectors, wrapping, the
`sys.monitoring` multiplexer, the event hub, writers) does not branch on a product, name a
product's private attributes, or clean up a product's state. When one consumer needs custom
behavior, it overrides or subscribes through its own hook (for example its own `ModuleWatchdog`
subclass). Shared code manages only the state it owns. The import detectors miss this, because
the dependency is on a name or a convention rather than on an import.

### LA-5 Integrations and products meet through core events

Contrib patches produce events through `ddtrace.internal.core`; products such as AppSec, IAST
and AI Guard subscribe while they are enabled. A contrib patch never imports product
implementation code or requires a product to be enabled. Details:
`.cursor/rules/isolated-responsibility.mdc`.

### LA-6 Product configuration belongs to the product (target)

Settings for a product live with that product, and shared code or the tracer does not read
another product's settings to decide what to do. The product pushes the configuration it needs
into shared code instead (for example through a registration call or an event).
`layers.json` currently classifies all of `ddtrace.internal.settings` as `internal-core`, so
the detector does not flag reads such as the tracer importing AppSec settings. Review them by
hand and do not add new ones.

### LA-7 Classification and exceptions are explicit decisions

- A new top-level module under `ddtrace/` gets a zone or a foundation entry in `layers.json`.
- A product-owned package under `ddtrace/internal/` gets its own zone mapping rather than
  falling under the `ddtrace.internal` catch-all.
- A new exception in `layers.json` is proposed only for a deliberate, lasting dependency, with
  its reason in a `//` entry and in the PR description, for a human to accept.

### LA-8 Public contracts keep their owner

Do not move public types such as `Tracer` or `Span` into shared code to satisfy the detector.
Preserve the public import paths (`ddtrace/trace/__init__.py`) and give other products a
narrower interface or an event instead.
