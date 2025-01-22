"""

Enabling
~~~~~~~~

The dd_trace_api integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(dd_trace_api=True)



Global Configuration
~~~~~~~~~~~~~~~~~~~~


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

"""
