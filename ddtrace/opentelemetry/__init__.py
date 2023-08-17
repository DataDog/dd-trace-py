"""
OpenTelemetry API
=================

The dd-trace-py library provides an implementation of the
`opentelemetry api <https://opentelemetry-python.readthedocs.io/en/latest/api/index.html>`_.
When ddtrace OpenTelemetry support is configured, all operations defined in the
OpenTelemetry trace api can be used to create, configure, and propagate a distributed trace.
All operations defined the opentelemetry trace api are configured to use the ddtrace global tracer (``ddtrace.tracer``)
and generate datadog compatible traces. By default all opentelemetry traces are submitted to a Datadog agent.


Configuration
-------------

When using ``ddtrace-run``, OpenTelemetry support can be enabled by setting
the ``DD_TRACE_OTEL_ENABLED`` environment variable to True (the default value is ``False``).

OpenTelemetry support can be enabled programmatically by setting ``DD_TRACE_OTEL_ENABLED=True``
and setting the ``ddtrace.opentelemetry.TracerProvider``. These configurations
must be set before any OpenTelemetry Tracers are initialized::

    import os
    # Must be set before ddtrace is imported!
    os.environ["DD_TRACE_OTEL_ENABLED"] = "true"

    from opentelemetry.trace import set_tracer_provider
    from ddtrace.opentelemetry import TracerProvider

    set_tracer_provider(TracerProvider())

    ...


Usage
-----

Datadog and OpenTelemetry APIs can be used interchangeably::

    # Sample Usage
    import opentelemetry
    import ddtrace

    oteltracer = opentelemetry.trace.get_tracer(__name__)

    with oteltracer.start_as_current_span("otel-span") as parent_span:
        parent_span.set_attribute("otel_key", "otel_val")
        with ddtrace.tracer.trace("ddtrace-span") as child_span:
            child_span.set_tag("dd_key", "dd_val")

    @oteltracer.start_as_current_span("span_name")
    def some_function():
        pass
"""
from ._trace import TracerProvider


__all__ = [
    "TracerProvider",
]
