# Local Bottle request benchmark

Measures one complete in-process WSGI request through a real Bottle application.
Routing, the tracing plugin, metadata extraction, subscriber work, span completion,
and body consumption are timed. Trace transport and telemetry workers are disabled
on both revisions. It measures elapsed time per request, not network throughput.

Run from the repository root:

```sh
scripts/run-benchmarks --scenario bottle_request \
  --baseline Datadog/dd-trace-py@6f5ec12fb138359bf415ba37f74d5cd1ab454c53 \
  --candidate . --artifacts ./benchmark-artifacts/bottle-request/
scripts/perf-analyze benchmark-artifacts/bottle-request/<run-id>/
```

The pinned baseline is PR #21342's merge base; it already uses the shared request
subscriber. This comparison measures the PR's wrapper simplification and response
handling changes, rather than the initial subscriber migration. Replace that SHA
when using this scenario for a different PR. The candidate includes local edits.
Use the printed run ID so analysis does not accidentally select another run.

The seven configurations are:

- `uninstrumented`: no tracing plugin, as a framework-only control.
- `disabled`: plugin installed with tracing disabled, exercising the entry guard.
- `success`: plain successful return with tracing.
- `returned-response`: returned HTTPResponse with status 201.
- `raised-response`: raised HTTPResponse with status 404 and Datadog error semantics.
- `raised-response-otel`: the same raised response with OpenTelemetry error semantics.
- `exception`: unhandled ValueError, converted to HTTP 500 by Bottle.

Requests carry fixed incoming trace and parent IDs. Before timing, each worker
validates the HTTP result and exactly one completed span for traced requests, or
zero spans for controls. It checks resource, propagation, error state, and original
error message. Completed traces are dropped at the writer boundary, so encoding
and agent delivery are excluded. Configured response-header tagging is omitted:
the PR fixes that behavior, so enabling it would compare different work.

All configurations run in separate pyperf worker processes. Run all configurations
for a final comparison; `--configs success,raised-response-otel` is useful while
iterating. Keep the machine idle and repeat results with material noise or control
drift. Interpret mean and variation together; local Docker measurements do not
establish production performance or a CI threshold.

This scenario is intentionally local: it is runnable by name without registering
a new CI suite or choosing an SLO before collecting stable measurements.
