"""Datadog tracing integration for the Temporal Python SDK.

This package provides a Datadog (``ddtrace``) tracing interceptor for the
Temporal Python SDK.

Usage (manual)::

    from temporalio.client import Client
    from ddtrace.contrib.internal.temporal import DatadogTracingInterceptor

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
"""

from .interceptor import DatadogTracingInterceptor
from .workflow_interceptor import disconnect_trace_span_from_workflow_context
from .workflow_interceptor import span_from_workflow_context
from .wrapped_tracer import FinishContext
from .wrapped_tracer import FinishResult


__all__ = [
    "DatadogTracingInterceptor",
    "FinishContext",
    "FinishResult",
    "disconnect_trace_span_from_workflow_context",
    "span_from_workflow_context",
]
