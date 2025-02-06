"""
To trace the falcon web framework, install the trace middleware::

    import falcon
    from ddtrace.trace import tracer
    from ddtrace.contrib.falcon import TraceMiddleware

    mw = TraceMiddleware(tracer, 'my-falcon-app')
    falcon.API(middleware=[mw])

You can also use the autopatching functionality::

    import falcon
    from ddtrace.trace import tracer, patch

    patch(falcon=True)

    app = falcon.API()

To disable distributed tracing when using autopatching, set the
``DD_FALCON_DISTRIBUTED_TRACING`` environment variable to ``False``.

**Supported span hooks**

The following is a list of available tracer hooks that can be used to intercept
and modify spans created by this integration.

- ``request``
    - Called before the response has been finished
    - ``def on_falcon_request(span, request, response)``


Example::

    import ddtrace.auto
    import falcon
    from ddtrace import config

    app = falcon.API()

    @config.falcon.hooks.on('request')
    def on_falcon_request(span, request, response):
        span.set_tag('my.custom', 'tag')

:ref:`Headers tracing <http-headers-tracing>` is supported for this integration.
"""
from ddtrace.contrib.internal.falcon.middleware import TraceMiddleware


__all__ = ["TraceMiddleware"]
