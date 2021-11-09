"""
The fastapi integration will trace requests to and from FastAPI.

Enabling
~~~~~~~~

The fastapi integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    from fastapi import FastAPI

    patch(fastapi=True)
    app = FastAPI()


If using Python 3.6, the legacy ``AsyncioContextProvider`` will have to be
enabled before using the middleware::

    from ddtrace.contrib.asyncio.provider import AsyncioContextProvider
    from ddtrace import tracer  # Or whichever tracer instance you plan to use
    tracer.configure(context_provider=AsyncioContextProvider())


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
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch"]
