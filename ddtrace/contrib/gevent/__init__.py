"""
The gevent integration adds support for tracing across greenlets.


The integration patches the gevent internals to add context management logic.

.. note::
    If ``ddtrace-run`` is not being used then be sure to ``import ddtrace.auto``
    before calling ``gevent.monkey.patch_all``.
    If ``ddtrace-run`` is being used then no additional configuration is required.


The integration also configures the global tracer instance to use a gevent
context provider to utilize the context management logic.

If custom tracer instances are being used in a gevent application, then
configure it with::

    from ddtrace.contrib.gevent import context_provider

    tracer.configure(context_provider=context_provider)


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :func:`patch_all()<ddtrace.patch_all>`.

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
        from .patch import patch
        from .patch import unpatch
        from .provider import GeventContextProvider

        context_provider = GeventContextProvider()

        __all__ = [
            "patch",
            "unpatch",
            "context_provider",
        ]
