"""
The gevent integration adds support for tracing across greenlets.

The integration patches the gevent internals to add context management logic.
It also configures the global tracer instance to use a gevent context
provider to utilize the context management logic.

If custom tracer instances are being used in a gevent application, then
configure it with::

    from ddtrace.contrib.gevent import context_provider

    tracer.configure(context_provider=context_provider)


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(gevent=True)


**Note:** these calls need to be performed before calling the gevent patching.

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
from ...utils.importlib import require_modules


required_modules = ["gevent"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .provider import GeventContextProvider
        from .patch import patch, unpatch

        context_provider = GeventContextProvider()

        __all__ = [
            "patch",
            "unpatch",
            "context_provider",
        ]
