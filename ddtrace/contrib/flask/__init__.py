"""
The Flask trace middleware will track request timings and templates. It
requires the `Blinker <https://pythonhosted.org/blinker/>`_ library, which
Flask uses for signalling.

To install the middleware, add::

    from ddtrace import tracer
    from ddtrace.contrib.flask import TraceMiddleware

and create a `TraceMiddleware` object::

    traced_app = TraceMiddleware(app, tracer, service="my-flask-app")

Here is the end result, in a sample app::

    from flask import Flask
    import blinker as _

    from ddtrace import tracer
    from ddtrace.contrib.flask import TraceMiddleware

    app = Flask(__name__)

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
