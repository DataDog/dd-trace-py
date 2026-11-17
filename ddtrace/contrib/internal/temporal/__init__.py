"""The temporal__ integration traces all workflows, activities, signals,
queries, updates, child workflows, and Nexus operations executed with the
``temporalio`` library.

Enabling
~~~~~~~~

The ``temporal`` integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(temporal=True)

    # use temporalio like usual

Workflow code can tag the ``RunWorkflow`` span or detach the next
``continue_as_new`` run from the current trace::

    from ddtrace.contrib.temporal import disconnect_trace_span_from_workflow_context
    from ddtrace.contrib.temporal import span_from_workflow_context

    span = span_from_workflow_context()
    if span is not None:
        span.set_tag("my.tag", value)

    disconnect_trace_span_from_workflow_context()
    workflow.continue_as_new(...)


Configuration
~~~~~~~~~~~~~

Use the following environment variables to configure the integration:

``DD_TRACE_TEMPORAL_ENABLED``
   Whether to enable the integration. Defaults to ``True``.

``DD_TEMPORAL_SERVICE`` / ``DD_TEMPORAL_SERVICE_NAME``
   The service name for ``temporal`` spans. By default, spans inherit the
   service name from their parent span or the global ``DD_SERVICE``.

Worker-side spans (``RunWorkflow``, ``RunActivity``, handlers) are always
kept: the client's sampling decision does not apply to the worker because
the two may run with different sampling rules.

.. __: https://github.com/temporalio/sdk-python
"""

from .interceptor import DatadogTracingInterceptor
from .workflow_interceptor import disconnect_trace_span_from_workflow_context
from .workflow_interceptor import span_from_workflow_context


__all__ = [
    "DatadogTracingInterceptor",
    "disconnect_trace_span_from_workflow_context",
    "span_from_workflow_context",
]
