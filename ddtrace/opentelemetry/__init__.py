"""
OpenTelemetry API
=================

The ``dd-trace-py`` library provides an implementation of the
`OpenTelemetry API <https://opentelemetry-python.readthedocs.io/en/latest/api/index.html>`_
for distributed tracing, metrics, and logs.

**Note:** Datadog-specific configurations take precedence over OpenTelemetry settings.
By default, telemetry data is routed to a Datadog Agent.
The minimum supported Datadog Agent version is ``v7.33.0`` (but recommended to use ``>=7.66`` for best performance).

**Note:** OpenTelemetry support is lazily loaded when the ``opentelemetry`` package is first imported. Ensure
``import opentelemetry...`` or ``from opentelemetry....`` is present in your code.


Tracing
-------

``dd-trace-py`` supports OpenTelemetry tracing by mapping OpenTelemetry spans to Datadog spans.
The ``TracerProvider`` sends Datadog-formatted payloads and is compatible only with the Datadog Agent
(it does not emit OTLP).

Enable OpenTelemetry tracing support by setting ``DD_TRACE_OTEL_ENABLED=true`` and using ``ddtrace.auto``
or ``ddtrace-run``. Manual configuration is also supported through ``ddtrace.opentelemetry.TracerProvider``.

For supported configurations, see `OpenTelemetry Tracing Configuration <https://docs.datadoghq.com/tracing/trace_collection/library_config/>`_.

Usage example::

    import os
    os.environ["DD_TRACE_OTEL_ENABLED"] = "true"

    import ddtrace.auto

    from opentelemetry import trace

    # Use tracing
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("operation") as span:
        span.set_attribute("key", "value")
        # Your code here


Metrics
-------

``dd-trace-py`` supports OpenTelemetry metrics. Metrics are emitted in OTLP format and can be routed
to any OTLP-compatible receiver (Datadog Agent, OpenTelemetry Collector, or directly to the Datadog intake).

Enable metrics support by setting ``DD_METRICS_OTEL_ENABLED=true`` and using ``ddtrace.auto`` or ``ddtrace-run``.
``ddtrace`` automatically configures the appropriate providers and exporters.

You must install an OTLP exporter (minimum supported version: ``v1.15.0``)::

    pip install opentelemetry-exporter-otlp>=1.15.0

For supported configurations, see `OpenTelemetry Metrics Configuration <https://docs.datadoghq.com/tracing/trace_collection/library_config/>`_.

Usage example::

    import os
    os.environ["DD_METRICS_OTEL_ENABLED"] = "true"
    import ddtrace.auto

    from opentelemetry import metrics

    # ddtrace automatically configures the MeterProvider
    meter = metrics.get_meter(__name__)
    counter = meter.create_counter("requests_total")
    counter.add(1, {"method": "GET"})


Logs
----

``dd-trace-py`` supports OpenTelemetry logs. Logs are emitted in OTLP format and can be routed
to any OTLP-compatible receiver (Datadog Agent, OpenTelemetry Collector, or directly to the Datadog intake).

Enable logs support by setting ``DD_LOGS_OTEL_ENABLED=true`` and using ``ddtrace.auto`` or ``ddtrace-run``.
``ddtrace`` automatically configures the appropriate providers and exporters.

You must install an OTLP exporter (minimum supported version: ``v1.15.0``)::

    pip install opentelemetry-exporter-otlp>=1.15.0

For supported configurations, see `OpenTelemetry Logs Configuration <https://docs.datadoghq.com/tracing/trace_collection/library_config/>`_.

Usage example::

    import os
    import logging
    os.environ["DD_LOGS_OTEL_ENABLED"] = "true"
    os.environ["DD_TRACE_OTEL_ENABLED"] = "true"
    import ddtrace.auto

    from opentelemetry import trace
    tracer = trace.get_tracer(__name__)

    # Logs within a trace context are automatically correlated
    with tracer.start_as_current_span("user_operation") as span:
        span.set_attribute("user.id", "12345")
        logging.info("User operation started", extra={"operation": "login"})
        # Your business logic here
        logging.info("User operation completed", extra={"status": "success"})


Supported Configuration
-----------------------

The Datadog SDK supports many of the configurations available in the OpenTelemetry SDK.
The following environment variables are supported:

- ``DD_{LOGS,TRACE,METRICS}_OTEL_ENABLED``
  Enable OpenTelemetry logs, metrics, or traces (default: ``false``).
  These features are off by default, allowing Datadog to track adoption by customer or service when the configuration is set to ``true``.

- ``OTEL_{METRICS,LOGS}_EXPORTER``
  This value defaults to ``otlp``. We can gauge the prevalence of other exporters by observing other values, such as ``prometheus``, ``console``, or ``none``.

- ``OTEL_METRIC_EXPORT_INTERVAL``
  Interval (in milliseconds) between metric exports.

- ``OTEL_METRIC_EXPORT_TIMEOUT``
  Timeout (in milliseconds) for metric export requests.

- ``OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE``
  For the best Datadog experience, we encourage customers to use **delta temporality** for monotonic sums, histograms, and exponential histograms.
  The default value in the Datadog SDKs is ``delta``, which differs from the OpenTelemetry specified default of ``cumulative``.
  Tracking this configuration helps understand how many customers prefer Cumulative Temporality and why.

- ``OTEL_EXPORTER_OTLP_{METRICS,LOGS}_ENDPOINT``
  When metrics export is enabled with ``DD_METRICS_OTEL_ENABLED=true``, this defines the target endpoint for metrics export (default: ``http://<agent_hostname>:4317``).

- ``OTEL_EXPORTER_OTLP_{METRICS,LOGS}_HEADERS``
  Optional headers for metrics or logs export in JSON format (default: ``{}``).

- ``OTEL_EXPORTER_OTLP_{METRICS,LOGS}_TIMEOUT``
  Request timeout in milliseconds for metrics or logs export (default: ``10000``).

- ``OTEL_EXPORTER_OTLP_{METRICS,LOGS}_PROTOCOL``
  OTLP protocol used for metrics or logs. Supported protocols are ``grpc`` (default) and ``http/protobuf``.

**Important:** Metrics and logs are exported using the OTLP protocol and can be routed to any OTLP-compatible receiver. See `Send OpenTelemetry Data to Datadog <https://docs.datadoghq.com/opentelemetry/setup/>`_ for more details. Traces, however, use Datadog’s custom MsgPack format and require a Datadog Agent.


Trace Mapping
-------------

OpenTelemetry spans are mapped to Datadog spans. This mapping is described by the following table, using the protocol buffer field names used in `OpenTelemetry <https://github.com/open-telemetry/opentelemetry-proto/blob/724e427879e3d2bae2edc0218fff06e37b9eb46e/opentelemetry/proto/trace/v1/trace.proto#L80>`_ and `Datadog <https://github.com/DataDog/datadog-agent/blob/dc4958d9bf9f0e286a0854569012a3bd3e33e968/pkg/proto/datadog/trace/span.proto#L7>`_.


.. list-table::
    :header-rows: 1
    :widths: 30, 30, 40

    * - OpenTelemetry
      - Datadog
      - Description
    * - ``trace_id``
      - ``traceID``
      -
    * - ``span_id``
      - ``spanID``
      -
    * - ``trace_state``
      - ``meta["tracestate"]``
      - Datadog-specific data is stored in the trace state using the ``dd=`` prefix
    * - ``parent_span_id``
      - ``parentID``
      -
    * - ``name``
      - ``resource``
      -
    * - ``kind``
      - ``meta["span.kind"]``
      -
    * - ``start_time_unix_nano``
      - ``start``
      -
    * - ``end_time_unix_nano``
      - ``duration``
      - Derived from the start and end times
    * - ``attributes[<key>]``
      - ``meta[<key>]``
      - Each OpenTelemetry attribute is stored as a Datadog tag (``meta``)
    * - ``links[]``
      - ``meta["_dd.span_links"]``
      -
    * - ``status``
      - ``error``
      - Derived from span status
    * - ``events[]``
      - ``meta["events"]``
      - If the v0.4 API is used, span events are stored as ``Span.span_events``

"""  # noqa: E501

from ddtrace.internal.opentelemetry.trace import TracerProvider


__all__ = [
    "TracerProvider",
]
