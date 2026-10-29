"""
To trace the falcon web framework, install the trace middleware::

    import falcon
    from ddtrace.contrib.falcon import TraceMiddleware

    mw = TraceMiddleware('my-falcon-app')
    falcon.API(middleware=[mw])

You can also use the autopatching functionality::

    import falcon
    from ddtrace.trace import patch

    patch(falcon=True)

    app = falcon.API()

To disable distributed tracing when using autopatching, set the
``DD_FALCON_DISTRIBUTED_TRACING`` environment variable to ``False``.

The falcon integration only traces WSGI applications. To trace a ``falcon.asgi.App``,
wrap it with the :ref:`ASGI middleware <asgi>`::

    import falcon.asgi
    from ddtrace.contrib.asgi import TraceMiddleware

    app = TraceMiddleware(falcon.asgi.App())

:ref:`Headers tracing <http-headers-tracing>` is supported for this integration.
"""

from ddtrace.contrib.internal.falcon.middleware import TraceMiddleware


__all__ = ["TraceMiddleware"]
