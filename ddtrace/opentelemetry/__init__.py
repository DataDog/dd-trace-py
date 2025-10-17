"""
OpenTelemetry API
=================

The dd-trace-py library provides an implementation of the
`OpenTelemetry API <https://opentelemetry-python.readthedocs.io/en/latest/api/index.html>`_
for distributed tracing, metrics, and logs.


Tracing
-------

dd-trace-py supports OpenTelemetry tracing by mapping OpenTelemetry spans to Datadog spans.
The TracerProvider sends custom Datadog payloads and is only compatible with a Datadog Agent (does not emit OTLP).

Enable OpenTelemetry tracing support by setting ``DD_TRACE_OTEL_ENABLED=true`` and using ``ddtrace.auto``
or ``ddtrace-run``. Manual configuration is also supported via ``ddtrace.opentelemetry.TracerProvider``.

For supported configurations, see `OpenTelemetry Tracing Configuration <placeholder-link>`_.

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

dd-trace-py supports OpenTelemetry metrics. Metrics are sent in OTLP format and can be routed to any OTLP receiver
(Datadog Agent, OpenTelemetry Collector, or directly to Datadog intake).

Enable metrics support by setting ``DD_METRICS_OTEL_ENABLED=true`` and using ``ddtrace.auto`` or ``ddtrace-run``.
ddtrace automatically configures the correct providers and exporters.

You must install your own OTLP exporter (minimum supported version is v1.15.0)::

    pip install opentelemetry-exporter-otlp>=1.15.0

For supported configurations, see `OpenTelemetry Metrics Configuration <placeholder-link>`_.

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

dd-trace-py supports OpenTelemetry logs. Logs are sent in OTLP format and can be routed to any OTLP receiver
(Datadog Agent, OpenTelemetry Collector, or directly to Datadog intake).

Enable logs support by setting ``DD_LOGS_OTEL_ENABLED=true`` and using ``ddtrace.auto`` or ``ddtrace-run``.
ddtrace automatically configures the correct providers and exporters.

You must install your own OTLP exporter (minimum supported version is v1.15.0)::

    pip install opentelemetry-exporter-otlp>=1.15.0

For supported configurations, see `OpenTelemetry Logs Configuration <placeholder-link>`_.

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
      - Datadog vendor-specific data is set in trace state using the ``dd=`` prefix
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
      - Derived from start and end time
    * - ``attributes[<key>]``
      - ``meta[<key>]``
      - Datadog tags (``meta``) are set for each OpenTelemetry attribute
    * - ``links[]``
      - ``meta["_dd.span_links"]``
      -
    * - ``status``
      - ``error``
      - Derived from status
    * - ``events[]``
      - ``meta["events"]``
      - Note: span events are stored as ``Span.span_events`` if v0.4 API is used


"""  # noqa: E501

from ddtrace.internal.opentelemetry.trace import TracerProvider


__all__ = [
    "TracerProvider",
]
