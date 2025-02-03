"""
The Cherrypy trace middleware will track request timings.
It uses the cherrypy hooks and creates a tool to track requests and errors


Usage
~~~~~
To install the middleware, add::

    from ddtrace import tracer
    from ddtrace.contrib.cherrypy import TraceMiddleware

and create a `TraceMiddleware` object::

    traced_app = TraceMiddleware(cherrypy, tracer, service="my-cherrypy-app")


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.cherrypy['distributed_tracing']

   Whether to parse distributed tracing headers from requests received by your CherryPy app.

   Can also be enabled with the ``DD_CHERRYPY_DISTRIBUTED_TRACING`` environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.cherrypy['service']

   The service name reported for your CherryPy app.

   Can also be configured via the ``DD_SERVICE`` environment variable.

   Default: ``'cherrypy'``


Example::
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


from ddtrace.contrib.internal.cherrypy.patch import TraceMiddleware
from ddtrace.contrib.internal.cherrypy.patch import get_version  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


def __getattr__(name):
    if name in ("get_version",):
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            message="Use ``import ddtrace.auto`` or the ``ddtrace-run`` command to configure this integration.",
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )

    if name in globals():
        return globals()[name]
    raise AttributeError("%s has no attribute %s", __name__, name)


__all__ = ["TraceMiddleware"]
