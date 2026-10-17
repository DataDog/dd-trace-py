"""
The bottle integration traces the Bottle web framework. Add the following
plugin to your app::

    import bottle
    from ddtrace import tracer
    from ddtrace.contrib.bottle import TracePlugin

    app = bottle.Bottle()
    plugin = TracePlugin(service="my-web-app")
    app.install(plugin)

:ref:`All HTTP tags <http-tagging>` are supported for this integration.

Request lifecycle
~~~~~~~~~~~~~~~~~

When installed on an application, the plugin traces the request from WSGI entry
through response iteration. The span finishes when the response is exhausted,
raises an error, or is explicitly closed. WSGI servers must close response
iterables when a request is interrupted. The final response status and headers
are recorded after Bottle processes the handler result and response hooks.
Unmatched requests use a resource such as ``GET 404``.

Plugins applied only to an individual route retain callback-scoped tracing.

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.bottle['distributed_tracing']

   Whether to parse distributed tracing headers from requests received by your bottle app.

   Can also be enabled with the ``DD_BOTTLE_DISTRIBUTED_TRACING`` environment variable.

   Default: ``True``


Example::

    from ddtrace import config

    # Enable distributed tracing
    config.bottle['distributed_tracing'] = True

"""

from ddtrace.contrib.internal.bottle.trace import TracePlugin


__all__ = ["TracePlugin"]
