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

"""

from ...utils.importlib import require_modules


required_modules = ["bottle"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .trace import TracePlugin

        __all__ = ["TracePlugin", "patch"]
