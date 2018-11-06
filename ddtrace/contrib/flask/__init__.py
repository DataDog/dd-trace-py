"""
The `Flask <http://flask.pocoo.org/>`_ integration will add tracing to all requests to your Flask application.

This integration will track the entire Flask lifecycle including user-defined endpoints, hooks,
signals, and templating rendering.

To configure tracing manually::

    from ddtrace import patch_all
    patch_all()

    from flask import Flask

    app = Flask(__name__)


    @app.route('/')
    def index():
        return 'hello world'


    if __name__ == '__main__':
        app.run()


You may also enable Flask tracing automatically via ddtrace-run::

    ddtrace-run python app.py

"""

from ...utils.importlib import require_modules


required_modules = ['flask']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        # DEV: We do this so we can `@mock.patch('ddtrace.contrib.flask._patch.<func>')` in tests
        from . import patch as _patch
        from .middleware import TraceMiddleware

        patch = _patch.patch
        unpatch = _patch.unpatch

        __all__ = ['TraceMiddleware', 'patch', 'unpatch']
