"""
The Flask trace middleware will track request timings and templates. It
requires the `Blinker <https://pythonhosted.org/blinker/>`_ library, which
Flask uses for signalling.

To install the middleware, add::

    from ddtrace import tracer
    from ddtrace.contrib.flask import TraceMiddleware

and create a `TraceMiddleware` object::

    traced_app = TraceMiddleware(app, tracer, service="my-flask-app", distributed_tracing=False)

Here is the end result, in a sample app::

    from flask import Flask
    import blinker as _

    from ddtrace import tracer
    from ddtrace.contrib.flask import TraceMiddleware

    app = Flask(__name__)

    traced_app = TraceMiddleware(app, tracer, service="my-flask-app", distributed_tracing=False)

    @app.route("/")
    def home():
        return "hello world"

Set `distributed_tracing=True` if this is called remotely from an instrumented application.
We suggest to enable it only for internal services where headers are under your control.
"""

from ...utils.importlib import require_modules


required_modules = ['flask']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .middleware import TraceMiddleware
        from .patch import patch

        __all__ = ['TraceMiddleware', 'patch']
