"""
The asgi__ middleware for tracing all requests to an ASGI-compliant application.

To configure tracing manually::

    from ddtrace.contrib.asgi import TraceMiddleware

    # app = <your asgi app>
    app = TraceMiddleware(app)

Then use ddtrace-run when serving your application. For example, if serving with Uvicorn::

    ddtrace-run uvicorn app:app

The middleware also supports using a custom function for handling exceptions for a trace::

    from ddtrace.contrib.asgi import TraceMiddleware

    def custom_handle_exception_span(exc, span):
        span.set_tag("http.status_code", 501)

    # app = <your asgi app>
    app = TraceMiddleware(app, handle_exception_span=custom_handle_exception_span)


To retrieve the request span from the scope of an ASGI request use the ``span_from_scope``
function::

    from ddtrace.contrib.asgi import span_from_scope

    def handle_request(scope, send):
        span = span_from_scope(scope)
        if span:
            span.set_tag(...)
        ...


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.asgi['distributed_tracing']

   Whether to use distributed tracing headers from requests received by your Asgi app.

   Default: ``True``

.. py:data:: ddtrace.config.asgi['service_name']

   The service name reported for your ASGI app.

   Can also be configured via the ``DD_SERVICE`` environment variable.

   Default: ``'asgi'``

.. __: https://asgi.readthedocs.io/
"""


from ddtrace.contrib.internal.asgi.middleware import TraceMiddleware
from ddtrace.contrib.internal.asgi.middleware import span_from_scope


__all__ = ["TraceMiddleware", "span_from_scope"]
