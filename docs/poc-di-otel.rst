DI over OpenTelemetry: Python POC
================================

This experiment extends the Collector-hosted remote configuration POC to the
complete Dynamic Instrumentation data path. RC delivers probe definitions;
execution, output delivery, diagnostics and symbol discovery complete DI.

Current result: the transport experiment works. A capture was also returned
by the Logs search API; visibility in Datadog Logs is acceptable for this POC.
Remaining qualification includes symbol UI discovery and an end-to-end run
using a wheel built from this source branch.

SDK contract
------------

``DD_DI_OTEL_ENABLED=true`` opts into the experimental transport. The default is
disabled. The OpenTelemetry SDK and HTTP/protobuf exporter must be installed;
without them the existing transport remains in use. This POC uses
``opentelemetry-exporter-otlp-proto-http==1.45.2`` and
``OTEL_EXPORTER_OTLP_ENDPOINT`` pointing at the Collector. Other protocols,
configuration combinations and older exporter versions are outside this POC.

DI activation and probe delivery continue through the existing RC path, with
``DD_TRACE_AGENT_URL`` pointing at the Collector's RC listener. The transport
flag does not install a probe or bypass DI capture limits and redaction.

* Dynamic spans and span attributes use the existing tracer with OTLP trace
  export enabled. Active-span behavior remains in the existing probe execution.
* Metric probes use OTel counters, gauges and histograms. Distribution probes
  map to histograms for this experiment. Instrumentation telemetry is separate.
* Snapshots, probe-generated messages, diagnostics and symbol batches use named
  OTel events through a dedicated provider, independent of application logging.
* Ordinary application logs can be collected from host files. The SDK adds no
  root logging handler for DI and does not require ``DD_LOGS_OTEL_ENABLED``.

The current OTel Events API is ``Logger.emit(event_name=...)``. Its wire
transport is OTLP Logs, not a separate events endpoint. The Collector routes
DI events to debugger intake; downstream visibility in Datadog Logs is allowed.

POC event envelope
------------------

The scope is ``com.datadoghq.dynamic_instrumentation``. Event names are
``dd.debugger.snapshot``, ``dd.debugger.log``, ``dd.debugger.diagnostic`` and
``dd.debugger.symbols``. All carry ``datadog.di.schema_version=1`` and
``datadog.di.payload`` containing lossless JSON of the existing, already
redacted payload. ``datadog.di.tags`` retains intake tags. Body is a display
message. Service, environment and version belong to the OTel resource.
Snapshot timestamps and available trace/span IDs are preserved in the envelope.

Symbol events carry ``event`` metadata and a structured ``symbols`` attachment.
The Collector reconstructs the existing compressed multipart intake request.
This Datadog-specific schema is experimental, not an OTel semantic convention.

Collector stack
---------------

Build a draft PR on the active Datadog extension RC branch. Use the standard
OTLP receiver. The Datadog exporter separates DI events before normal log
mapping and calls the extension's opt-in DI sender, which owns intake
credentials and delivery. Unknown DI schema versions or unavailable DI support
must fail closed: never fall back to ordinary log indexing. Application logs
continue through the ordinary logs pipeline. Probe updates/removals use RC;
they do not become outbound events.

Validation
----------

Verify flag-off and missing-exporter behavior; every event kind; counter, gauge
and histogram output; trace/span correlation; and lossless capture JSON. Run a
real backend probe through Collector RC and verify install, capture and removal,
with matching DI UI results. Assert that the normal logs consumer receives zero
DI records, including mixed batches and rejected DI envelopes. Keep snapshots
out of debug exporters and host log files as well.

The first pass is single-process HTTP/protobuf. Forking, retry tuning, payload
limits across processors, shutdown ordering, attribute truncation and concurrent
product use need follow-up before release. Shared debugger uploaders also serve
Exception Replay and Code Origin; their behavior needs qualification before
enabling this flag alongside those products. Queue acceptance is not proof of
backend ingestion.

The live transport check used a released Python 4.16.4 native wheel with the
Python transport changes applied, plus OTel 1.45.2. It verified real RC probe
installation/capture/removal, active dynamic spans, span attributes, metric
probe export, named DI events and host-file log delivery. Symbol events were
emitted without intake rejection; full symbol UI discovery remains unverified.
Focused Python 3.13 source checks passed: nine DI tests with OTel 1.45.2
and 24 existing uploader regression tests. This is not full-suite coverage.
