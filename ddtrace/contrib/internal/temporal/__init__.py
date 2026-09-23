"""Datadog tracing integration for the Temporal Python SDK.

This package provides a Datadog (``ddtrace``) tracing interceptor for the
Temporal Python SDK.

Usage (manual)::

    from temporalio.client import Client
    from ddtrace.contrib.temporal import DatadogTracingInterceptor

    interceptor = DatadogTracingInterceptor(
        service_name="my-service",
        extra_tags={"deployment.environment": "prod"},
    )
    client = await Client.connect("localhost:7233", interceptors=[interceptor])

Or enable automatically via :func:`ddtrace.patch` (auto-injects a default
interceptor into every ``Client`` and registers the ``ddtrace`` namespace as
passthrough with the Temporal workflow sandbox so workflow code can call
:func:`span_from_workflow_context`)::

    from ddtrace import patch
    patch(temporal=True)

``patch(logging=True)`` is recommended separately to opt in to ``dd.trace_id``
log injection.

Configuration
~~~~~~~~~~~~~

.. envvar:: DD_TRACE_TEMPORAL_DISABLE_SIGNAL_TRACING

   Whether to suppress Temporal signal spans. Default: ``False``.

.. envvar:: DD_TRACE_TEMPORAL_DISABLE_QUERY_TRACING

   Whether to suppress Temporal query spans. Default: ``False``.

.. envvar:: DD_TRACE_TEMPORAL_DISABLE_UPDATE_TRACING

   Whether to suppress Temporal update spans. Default: ``False``.

Values passed to the corresponding ``DatadogTracingInterceptor`` constructor
arguments take precedence over these integration settings.
"""

from .interceptor import DatadogTracingInterceptor


__all__ = ["DatadogTracingInterceptor"]
