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
