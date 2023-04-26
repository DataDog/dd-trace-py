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
the ``DD_TRACE_OTEL_ENABLED`` environment variable (the default value is ``False``).

OpenTelemetry support can be programmatically enabled by setting the ``OTEL_PYTHON_CONTEXT`` environment variable
to ``ddcontextvars_context`` and setting the ``ddtrace.opentelemetry.TracerProvider``. These configurations
must be set before any OpenTelemetry Tracers are initialized::

    from opentelemetry.trace import set_tracer_provider
    from ddtrace.opentelemetry import TracerProvider

    set_tracer_provider(TracerProvider())
    # Replaces the default otel api runtime context with DDRuntimeContext
    # https://github.com/open-telemetry/opentelemetry-python/blob/v1.16.0/opentelemetry-api/src/opentelemetry/context/__init__.py#L53
    os.environ["OTEL_PYTHON_CONTEXT"] = "ddcontextvars_context"

    ...


Usage
-----

Datadog and OpenTelemetry APIs can be used interchangeably::

    # Sample Usage
    import opentelemetry
    import ddtrace

    oteltracer = opentelemetry.trace.get_tracer(__name__)

    with oteltracer.start_as_current_span("otel-span") as parent_span:
        otel_span.set_attribute("otel_key", "otel_val")
        with ddtrace.trace("ddtrace-span") as child_span:
            child_span.set_tag("dd_key", "dd_val")

    @oteltracer.start_as_current_span("span_name")
    def some_function():
        pass
"""
from ._trace import TracerProvider


__all__ = [
    "TracerProvider",
]
