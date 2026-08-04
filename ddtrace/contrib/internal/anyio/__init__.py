"""
This integration republishes the active span to the OpenTelemetry thread
context when AnyIO runs synchronous callables in worker threads, preserving
trace and span correlation for host-profiler samples collected off the event
loop.


Enabling
~~~~~~~~

The anyio integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(anyio=True)
"""
