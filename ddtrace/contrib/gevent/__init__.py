"""
The gevent integration adds support for tracing across greenlets.

.. note::
    If ``ddtrace-run`` is not being used then be sure to ``import ddtrace.auto``
    before importing from the gevent library.
    If ``ddtrace-run`` is being used then no additional configuration is required.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(gevent=True)


Example of the context propagation::

    def my_parent_function():
        with tracer.trace("web.request") as span:
            span.service = "web"
            gevent.spawn(worker_function)


    def worker_function():
        # then trace its child
        with tracer.trace("greenlet.call") as span:
            span.service = "greenlet"
            ...

            with tracer.trace("greenlet.child_call") as child:
                ...
"""
from ...internal.utils.importlib import require_modules


required_modules = ["gevent"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from ...provider import DefaultContextProvider as _DefaultContextProvider
        from .patch import get_version
        from .patch import patch
        from .patch import unpatch

        context_provider = _DefaultContextProvider()

        __all__ = ["patch", "unpatch", "context_provider", "get_version"]
