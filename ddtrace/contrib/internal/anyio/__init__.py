"""Publish native trace context for synchronous AnyIO workers.

AnyIO already propagates Python context variables. On Linux runtimes without
native context watching, this integration also publishes the active trace
context for native consumers while a worker callable runs.

The integration is enabled automatically with ``ddtrace-run`` or
``import ddtrace.auto``. It can also be enabled with ``patch(anyio=True)``.
Set ``DD_TRACE_ANYIO_ENABLED=false`` to disable automatic instrumentation.
"""
