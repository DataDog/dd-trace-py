"""Publish native trace context for Trio tasks and worker threads.

Trio already propagates Python context variables between tasks and into worker
threads. On Linux runtimes without native context watching, this integration
also publishes the active trace context for native consumers.

The integration is enabled automatically with ``ddtrace-run`` or
``import ddtrace.auto``. It can also be enabled with ``patch(trio=True)``.
Set ``DD_TRACE_TRIO_ENABLED=false`` to disable automatic instrumentation.
"""
