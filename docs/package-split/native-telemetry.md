# Native telemetry extraction: Phase A inventory

Status: inventory and proposal. No code moved. Names and boundaries are open
decisions under `docs/package-split/plan.md`; nothing here pre-approves them.

Target: the native telemetry implementation that backs
`ddtrace.internal.telemetry`, extracted as the first native slice of the
package split (`datadog-apm-python` destination repository).

## What is in scope today

Rust side (inside the single `src/native` crate that builds
`ddtrace.internal.native._native`):

- `src/native/telemetry.rs` — 559 LOC, registered from `lib.rs` via
  `telemetry::register_telemetry`. Exposes `TelemetryWorker`, `MetricContext`,
  `MetricNamespace`, `MetricType`, `ConfigurationOrigin`, `LogLevel`.
- External crate dependencies: `libdd_telemetry` (libdatadog),
  `libdd_shared_runtime` (`SharedRuntime`, `BlockingRuntime`, `WorkerHandle`),
  `libdd_capabilities_impl`, `native_proc_macro` (in-crate), `pyo3`.

Python side:

- Pure-Python surface re-exported through `ddtrace.internal.native.__init__`
  (the wrapper package that eagerly imports `_native`).
- Consumers of the native telemetry symbols: exactly two files,
  `ddtrace/internal/telemetry/writer.py` and `ddtrace/internal/telemetry/metrics.py`.
  Both already import the native symbols lazily (`TYPE_CHECKING` blocks plus
  in-method imports); `TelemetryWriter` is documented as a "thin shim" over the
  native worker. The Python side is therefore already shaped as an adapter.
- `ddtrace.internal.telemetry` as a whole is a shared foundation: its used
  surface (73 symbols per `api-usage`, evidence
  `.package-split/api-usage/api-usage.json`) is consumed by every product zone
  (appsec, IAST, profiling, CI Visibility, ... via `product_activated`,
  `add_count_metric`, configuration reporting) plus `ddtrace.internal.settings._config`.

## Couplings that block a clean standalone build (the blurry lines)

1. **Shared runtime.** `TelemetryWorker` is bound to `libdd_shared_runtime` and
   the in-crate `shared_runtime` module (`SharedRuntimePy`,
   `ensure_after_fork_child`). The shared runtime is also used by the writer,
   HTTP client, remote config, symbol DB and live debugging uploaders. It is
   shared native core, not telemetry-exclusive; telemetry cannot take a private
   copy without duplicating shared state.
2. **Data pipeline link.** `TraceExporterBuilder.enable_telemetry(...)` wires a
   `TelemetryWorker` handle into the trace exporter (`data_pipeline/`), so the
   writer's agent path and telemetry share the same runtime handle.
3. **Single-extension packaging.** `telemetry.rs` is compiled unconditionally
   into `_native`. The crate already has feature-gated carve-out precedents:
   `crashtracker`, `ffe`, `profiling` (Cargo features, with
   `try/except ImportError` on the Python side and exclusion in serverless
   builds). `ffe.rs` (248 LOC) is the closest precedent in size and shape.

## Open decisions (require approval before Phase B/C)

- Carve-out mechanism: Cargo feature on `src/native` versus a separate crate
  or cdylib in the destination workspace.
- Destination layer: shared native (`ddtrace_internal_native`-side) versus a
  telemetry-owning package; where the shared runtime dependency lands in that
  split.
- Whether the Python package `ddtrace.internal.telemetry` moves in the same
  slice or stays as the bridge, and the compatibility window.
- Behavioral fixture set: `tests/telemetry/*` (writer behavior, metric
  contexts, app-closing events) is the source for the `contracts` gate
  fixtures; `tests/internal/test_process_tags.py` and settings consumers pin
  cross-module behavior.

## Proposed first slice (Phase B per the skill)

1. Gate the telemetry module behind a Cargo feature on `src/native` mirroring
   the `ffe` pattern (feature in `Cargo.toml`, conditional `mod telemetry` +
   registration in `lib.rs`, `try/except ImportError` on the Python import
   sites that already exist).
2. Run the `boundaries`, `lifecycle` and `artifacts` gates on a candidate tree
   with the feature on and off, including the native-free build variant.
3. Port the consumer-relevant behavior from `tests/telemetry/` into behavioral
   fixtures for the `contracts` gate.
4. Re-run `api-usage`/`api-surface` after the change to record the new used
   surface of both `ddtrace.internal.telemetry` and
   `ddtrace.internal.native`.

## Evidence

- `scripts/package-split api-usage --module ddtrace.internal.telemetry`
  (production) and with `--source-path tests`; evidence under
  `.package-split/api-usage/`.
- `scripts/package-split api-surface`; evidence under
  `.package-split/api-surface/` (telemetry.rs cluster: 2 consumer files,
  internal-core zone; `shared_runtime/` cluster: 6 consumer files).
- Static import scan of `src/native` and `ddtrace/` (2025-11-05, branch
  `gnufede-ddtracepy-split-native-telemetry`).
