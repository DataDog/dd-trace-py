# Integration behavioral validation

Use the coverage section for a new integration. Read the remaining sections only when the integration
has those behaviors or the change affects them; apply the same scope when reviewing a PR.

## Coverage and reuse

- Inspect the instrumented library's public entry points and existing SDK/OTel instrumentation.
  Map core operations to hooks, supported dependency versions, and tests; state unsupported paths.
  Include distinct client/worker, sync/async, and newer entry points where applicable.
- Compare upstream instrumentation's operation boundaries and propagation before duplicating it.
  Reuse hooks where compatible; do not assume upstream covers every operation or fits ddtrace's provider.
- Search shared helpers and nearby integrations before adding propagation, wrapping, or test utilities.
  Reuse standard fixtures; explain a specialized helper when the shared one cannot represent the behavior.

## Composition and application state

- Exercise automatic patching and explicit SDK instrumentation together, including configurations
  merged across clients and workers. Repeated patch calls alone do not establish deduplication.
  Define coexistence with upstream instrumentation so one operation does not get duplicate spans.
- Preserve caller-owned options and containers. Test custom clients/runners and, where relevant,
  sandbox restrictions created before instrumentation; changing global defaults may not affect them.
- Verify return values, exceptions, and cancellation retain the library's behavior with tracing enabled.

## Propagation and OpenTelemetry

- Test local, remote, and absent parents; optional/empty carriers; and each carrier representation used
  by the library. Check header keys and payload encoding as well as the propagation fields inside them.
- When upstream OTel instrumentation exists, test native-to-OTel and OTel-to-native propagation with
  default settings. A configurable workaround is a limitation to document, not proof defaults interoperate.
- Keep the installed ddtrace TracerProvider unchanged. Test nested spans through both APIs, active
  parent visibility, baggage, and context restoration on success and failure in each execution environment.
  Installing a different global provider to make a test pass does not establish interoperability.
- Treat a private ContextVar as integration state, not proof of active tracing context. Verify parenting
  across task/thread or sandbox boundaries where the library executes user code.

## Durable or long-running operations

- Define which execution or attempt owns each span and when it finishes. Test replay, retries, restart,
  cancellation, eviction, and shutdown where supported; distinguish infrastructure lifecycle events
  from application failures. Check handlers that begin during replay and continue into live execution.
- Verify replay does not export duplicate historical spans or invent new historical durations.
  Deterministic span IDs alone do not prevent duplicate exports.
- With default tracer settings, verify finished children can export before a long-lived parent ends.
  Use a small number of children so the test does not accidentally rely on a partial-flush threshold;
  inspect pending spans before shutdown flushing can hide retention.

## Output and documentation evidence

- Use shared fixtures and test-agent snapshots to verify serialized span count, trace/parent IDs,
  names, service/resource, kind, errors, and sampling. An in-memory collector alone misses export behavior.
  Pair mocked boundary tests with a small realistic execution where SDK scheduling or replay matters.
- When adding custom span creation or attribution, inspect emitted instrumentation telemetry for both
  span creation and completion. Setting a component after creation may not attribute both counters alike.
- Check default behavior and inherited sampling decisions against documentation; do not add force-keep
  behavior merely to satisfy an inaccurate claim. Cover dependency boundaries through the existing suite.
- Match the PR description, inline explanations, and integration docstring to tested behavior and limits.
  Update applicable entries in docs/integrations.rst, the support table in docs/index.rst, dependency
  metadata, and release notes; document public options and interoperability limitations.
