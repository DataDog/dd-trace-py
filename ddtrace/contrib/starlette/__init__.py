"""
The Starlette integration will trace requests to and from Starlette.


Enabling
~~~~~~~~

The starlette integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    from starlette.applications import Starlette

    patch(starlette=True)
    app = Starlette()


If using Python 3.6, the legacy ``AsyncioContextProvider`` will have to be
enabled before using the middleware::

    from ddtrace.contrib.asyncio.provider import AsyncioContextProvider
    from ddtrace import tracer  # Or whichever tracer instance you plan to use
    tracer.configure(context_provider=AsyncioContextProvider())


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.starlette['distributed_tracing']

   Whether to parse distributed tracing headers from requests received by your Starlette app.

   Can also be enabled with the ``DD_STARLETTE_DISTRIBUTED_TRACING`` environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.starlette['analytics_enabled']

   Whether to analyze spans for starlette in App Analytics.

   Can also be enabled with the ``DD_STARLETTE_ANALYTICS_ENABLED`` environment variable.

   Default: ``None``

.. py:data:: ddtrace.config.starlette['service_name']

   The service name reported for your starlette app.

   Can also be configured via the ``DD_SERVICE`` environment variable.

   Default: ``'starlette'``

.. py:data:: ddtrace.config.starlette['request_span_name']

   The span name for a starlette request.

   Default: ``'starlette.request'``


Example::

    from ddtrace import config

    # Enable distributed tracing
    config.starlette['distributed_tracing'] = True

    # Override service name
    config.starlette['service_name'] = 'custom-service-name'

    # Override request span name
    config.starlette['request_span_name'] = 'custom-request-span-name'

"""
from ...internal.utils.importlib import require_modules


required_modules = ["starlette"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch"]
