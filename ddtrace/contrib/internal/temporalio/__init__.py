"""
The Temporal integration traces workflow and activity operations performed with
the ``temporalio`` library.


Enabling
~~~~~~~~

The ``temporalio`` integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch()<ddtrace.patch>` to manually enable the
integration::

    from ddtrace import patch

    patch(temporalio=True)
"""
