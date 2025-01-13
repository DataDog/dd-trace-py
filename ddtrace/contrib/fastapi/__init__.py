"""
The fastapi integration will trace requests to and from FastAPI.

Enabling
~~~~~~~~

The fastapi integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Use DD_TRACE_<INTEGRATION>_ENABLED environment variable to enable or disable this integration.

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


# Required to allow users to import from  `ddtrace.contrib.fastapi.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001

# Expose public methods
from ddtrace.contrib.internal.fastapi.patch import get_version
from ddtrace.contrib.internal.fastapi.patch import patch
from ddtrace.contrib.internal.fastapi.patch import unpatch


__all__ = ["patch", "unpatch", "get_version"]
