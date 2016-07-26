"""
The flask trace middleware will track request timings and templates. It
requires the `Blinker <https://pythonhosted.org/blinker/>`_ library, which
Flask uses for signalling.

To install the middleware, do the following::

    from flask import Flask
    import blinker as _

    from ddtrace import tracer
    from ddtrace.contrib.flask import TraceMiddleware

    app = Flask(...)

    traced_app = TraceMiddleware(app, tracer, service="my-flask-app")

    @app.route("/")
    def home():
        return "hello world"

"""

from ..util import require_modules

required_modules = ['flask']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .middleware import TraceMiddleware

        __all__ = ['TraceMiddleware']
