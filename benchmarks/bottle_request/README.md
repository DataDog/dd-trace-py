# Local Bottle request benchmark

Measures one complete in-process WSGI request through a real Bottle application.
Routing, the tracing plugin, metadata extraction, subscriber work, span completion,
and body consumption are timed. Trace transport and telemetry workers are disabled
on both revisions. It measures elapsed time per request, not network throughput.

Run from the repository root, choosing a baseline appropriate to the change:

```sh
bottle_baseline_ref=$(git merge-base HEAD origin/main)
scripts/run-benchmarks --scenario bottle_request \
  --baseline "Datadog/dd-trace-py@${bottle_baseline_ref}" \
  --candidate . --artifacts ./benchmark-artifacts/bottle-request/
scripts/perf-analyze benchmark-artifacts/bottle-request/<run-id>/
```

The example compares the merge base with the local checkout. An explicit published
version or another Git ref can also be used as the baseline. Use the printed run ID
so analysis does not accidentally select another run.

The configurations are:

- `uninstrumented`: no tracing plugin, as a framework-only control.
- `disabled`: plugin installed with tracing disabled, exercising the entry guard.
- `success`: plain string return with tracing, producing HTTP 200.
- `returned-response`: returned HTTPResponse with status 201.
- `raised-response`: raised HTTPResponse with status 404 and Datadog error semantics.
- `raised-response-otel`: the same raised response with OpenTelemetry error semantics.
- `exception`: unhandled ValueError, converted to HTTP 500 by Bottle.
- `success-no-propagation`: the same traced HTTP 200 request as `success`, without
  incoming distributed tracing headers. Compare these two cases to measure
  request overhead with and without an incoming context.

All configurations except `success-no-propagation` include incoming Datadog trace,
parent, and sampling-priority headers. Requests cycle through 64 precomputed WSGI
environments. When headers are enabled, the pool has distinct trace and parent IDs,
so adjacent requests use different contexts without generating IDs in the timed
loop. The pool repeats; IDs are not globally unique across the benchmark.

One untimed request primes Bottle's route and plugin caches. Instrumentation
correctness is covered by the Bottle integration tests; the benchmark retains
uninstrumented and disabled controls to make missing tracing overhead visible.
Completed traces are dropped at the writer boundary, so encoding and agent
delivery are excluded. Response-header tagging is not configured.

All configurations run in separate pyperf worker processes. Run all configurations
for a final comparison; `--configs success,success-no-propagation` is useful for
comparing incoming context propagation. Keep the machine idle and repeat results with
material noise or control drift. Interpret mean and variation together; local
Docker measurements do not establish production performance or a CI threshold.

This scenario is runnable by name without registering a new CI suite or choosing
an SLO before collecting stable measurements.
