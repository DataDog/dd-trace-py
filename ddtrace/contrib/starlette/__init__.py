"""
The Starlette__ integration is provided by a ASGI middleware.

To configure tracing manually::

    from ddtrace.contrib.asgi import TraceMiddleware

    app = Starlette()
    app.add_middleware(TraceMiddleware)

If using Python 3.6, the legacy ``AsyncioContextProvider`` will have to be
enabled before using the middleware::

    from ddtrace.contrib.asyncio.provider import AsyncioContextProvider
    from ddtrace import tracer  # Or whichever tracer instance you plan to use
    tracer.configure(context_provider=AsyncioContextProvider())


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.asgi['distributed_tracing_enabled']

   Whether to use distributed tracing headers from requests received by your ASGI app.

   Default: ``True``

.. py:data:: ddtrace.config.asgi['analytics_enabled']

   Whether to analyze spans for your ASGI app in App Analytics.

   Can also be enabled with the ``DD_TRACE_ASGI_ANALYTICS_ENABLED`` environment variable.

   Default: ``None``

.. py:data:: ddtrace.config.asgi['service_name']

   The service name reported for your ASGI app.

   Can also be configured via the ``DD_SERVICE`` environment variable.

   Default: ``'asgi'``

.. __: https://www.starlette.io/
"""
