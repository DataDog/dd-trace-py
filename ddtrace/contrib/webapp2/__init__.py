"""This trace middleware will track request timings for webapp2

To install the middleware, prepare your WSGI application and then apply
the middleware::

    import webapp2

    from ddtrace import tracer
    from ddtrace.contrib.webapp2 import Webapp2TraceMiddleware

    app = webapp2.WSGIApplication(...)

    traced_app = Webapp2TraceMiddleware(app, tracer, service='example-service')

Use the `traced_app` as you would your normal `app`.
"""

from ...utils.importlib import require_modules

required_modules = ['webapp2']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .middleware import Webapp2TraceMiddleware

        __all__ = [
            'Webapp2TraceMiddleware'
        ]
