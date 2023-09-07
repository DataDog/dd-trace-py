"""
The fastapi integration will trace requests to and from FastAPI.

Enabling
~~~~~~~~

The fastapi integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :func:`patch_all()<ddtrace.patch_all>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    from fastapi import FastAPI

    patch(fastapi=True)
    app = FastAPI()


On Python 3.6 and below, you must enable the legacy ``AsyncioContextProvider`` before using the middleware::

    from ddtrace.contrib.asyncio.provider import AsyncioContextProvider
    from ddtrace import tracer  # Or whichever tracer instance you plan to use
    tracer.configure(context_provider=AsyncioContextProvider())


When registering your own ASGI middleware using FastAPI's ``add_middleware()`` function,
keep in mind that Datadog spans close after your middleware's call to ``await self.app()`` returns.
This means that accesses of span data from within the middleware should be performed
prior to this call.


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.fastapi['service_name']

   The service name reported for your fastapi app.

   Can also be configured via the ``DD_SERVICE`` environment variable.

   Default: ``'fastapi'``

.. py:data:: ddtrace.config.fastapi['request_span_name']

   The span name for a fastapi request.

   Default: ``'fastapi.request'``


Example::

    from ddtrace import config

    # Override service name
    config.fastapi['service_name'] = 'custom-service-name'

    # Override request span name
    config.fastapi['request_span_name'] = 'custom-request-span-name'

"""
from ...internal.utils.importlib import require_modules


required_modules = ["fastapi"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import get_version
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]
