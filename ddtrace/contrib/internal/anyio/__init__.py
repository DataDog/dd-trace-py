"""
This integration republishes the active trace context when AnyIO runs
synchronous callables in worker threads, so operations executed off the event
loop are attributed to the trace that scheduled them.


Enabling
~~~~~~~~

The anyio integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(anyio=True)
"""
