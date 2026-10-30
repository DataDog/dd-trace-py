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

The plugin traces the matched route callback. The span finishes when the callback
returns or raises, including when it returns a response iterator. Response
iteration, later response hooks, and transport delivery occur outside this span.
Bottle manages plugin ordering, route skip settings, and callback-cache resets.
Unmatched requests and routes that skip the plugin do not create a Bottle span.

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
