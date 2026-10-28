# Invocation-end EVP flushing

`_telemetry.FLUSH_API_VERSION == 1` identifies the internal integration contract
used by `datadog-lambda-python`. `_telemetry.flush(timeout)` accepts a total caller
waiting budget in seconds and returns `completed`, `failed`, or `timed_out`.
It is not a public OpenFeature provider API. Importing the boundary neither imports
the OpenFeature SDK nor creates a provider or writer.

All live evaluation writers and exposure writers register weak references. A
flush snapshots accepted work and delivery progress; queue removal is not delivery.
Evaluation draining is serialized through aggregation, and each writer serializes
batch snapshots through endpoint acknowledgement. A completion requires 2xx
acceptance for all applicable batches. Missing routes, failed encoding, policy
drops after acceptance, and rejected or ambiguous deliveries are incomplete.
Failures observed while coordinating the snapshot are also included. A failed
invocation does not permanently poison subsequent flushes.

One lazily created coordinator serves all invocation-end requests. Calls share a
single monotonic deadline across registry/progress coordination and waiting for
aggregation, route discovery, and delivery. The coordinator performs the writers'
existing transport operations; the caller does not perform network I/O. Requests
coalesce by writer, so repeated timeouts cannot accumulate helper workers or
unbounded request queues. Providers, hooks, polling and ordinary writer workers
remain active across warm invocations.

Timeout stops waiting, not delivery. Queued work and in-flight batches remain with
their owners and can finish after the caller returns or after a warm thaw. Timeout
never puts an uncertain batch back in a queue, initiates an alternative route, or
adds retries. Existing transport rules still determine safe fallback and retries.
No durability is promised across process termination or network failure. Endpoint
acceptance does not imply immediate visibility in the Datadog UI.

Forked processes reset coordinator requests and evaluation progress. The
coordinator does not restart inherited work in the child. Its bounded idle wait
allows the native thread fork protocol to stop it; transport timeouts continue
to govern outstanding network operations during a fork.

## Release coordination

The first published tracer version, Lambda library version, and compatible layer
ARN/version must be recorded together after release artifacts are available.
No published combination is claimed by this development branch. The Lambda side
checks contract version 1 and safely skips older tracers. Release the tracer
capability first, then the Lambda wrapper and a layer containing both changes.
Python versions whose tracer release lacks contract version 1 remain a no-op.

Before declaring support, validate a short successful invocation and a short
raising invocation on the published layer, repeat them in a warm sandbox, and
confirm ingestion of both evaluation and exposure events for a unique test
subject. Cover local Agent EVP delivery separately from agentless direct fallback.
Record the runtime, architecture, tracer version, Lambda library version, layer
ARN, invocation IDs and ingestion evidence in the release record.
