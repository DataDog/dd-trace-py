"""
The Cherrypy trace middleware will track request timings.
It uses the cherrypy hooks and creates a tool to track requests and errors

To install the middleware, add::

    from ddtrace import tracer
    from ddtrace.contrib.cherrypy import TraceMiddleware

and create a `TraceMiddleware` object::

    traced_app = TraceMiddleware(cherrypy, tracer, service="my-cherrypy-app")

Here is the end result, in a sample app::

    import cherrypy

    from ddtrace import tracer, Pin
    from ddtrace.contrib.cherrypy import TraceMiddleware
    TraceMiddleware(cherrypy, tracer, service="my-cherrypy-app")

    @cherrypy.tools.tracer()
    class HelloWorld(object):
        def index(self):
            return "Hello World"
        index.exposed = True

    cherrypy.quickstart(HelloWorld())
"""

from ..util import require_modules

required_modules = ['cherrypy']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .middleware import TraceMiddleware

        __all__ = ['TraceMiddleware']
