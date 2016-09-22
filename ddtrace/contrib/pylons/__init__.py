"""
The pylons trace middleware will track request timings. To
install the middleware, prepare your WSGI application and do
the following::

    from pylons.wsgiapp import PylonsApp

    from ddtrace import tracer
    from ddtrace.contrib.pylons import PylonsTraceMiddleware

    app = PylonsApp(...)

    traced_app = PylonsTraceMiddleware(app, tracer, service="my-pylons-app")

Then you can define your routes and views as usual.
"""

from .middleware import PylonsTraceMiddleware


__all__ = ['PylonsTraceMiddleware']
