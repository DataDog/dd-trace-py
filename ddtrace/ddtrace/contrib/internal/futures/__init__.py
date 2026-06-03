"""
The ``futures`` integration propagates the current active tracing context
to tasks spawned using a :class:`~concurrent.futures.ThreadPoolExecutor`.
The integration ensures that when operations are executed in another thread,
those operations can continue the previously generated trace.


Enabling
~~~~~~~~

The futures integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(futures=True)
"""
