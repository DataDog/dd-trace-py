"""Sub-application variant of the Flask test app.

All views are reused from ``app.py``, but grouped endpoints are re-registered on
per-prefix sub-Flask-apps mounted under ``werkzeug.middleware.dispatcher.DispatcherMiddleware``
instead of being declared directly on the root app. This exercises the
DispatcherMiddleware code paths in the Flask integration (endpoint discovery,
span resource with script_root, etc.).

Bare (no-trailing-slash) aliases for ``/asm`` are kept on the root app so the
reachable URL surface stays equivalent to ``app.py`` even though the raw rule
strings differ between variants.
"""

import os

from flask import Flask
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from tests.appsec.contrib_appsec.flask_app.app import app as flat_app
from tests.appsec.contrib_appsec.flask_app.app import buggy_endpoint
from tests.appsec.contrib_appsec.flask_app.app import exception_group_block
from tests.appsec.contrib_appsec.flask_app.app import index
from tests.appsec.contrib_appsec.flask_app.app import login_user
from tests.appsec.contrib_appsec.flask_app.app import login_user_sdk
from tests.appsec.contrib_appsec.flask_app.app import multi_view
from tests.appsec.contrib_appsec.flask_app.app import new_service
from tests.appsec.contrib_appsec.flask_app.app import rasp
from tests.appsec.contrib_appsec.flask_app.app import redirect
from tests.appsec.contrib_appsec.flask_app.app import redirect_httpx
from tests.appsec.contrib_appsec.flask_app.app import redirect_httpx_async
from tests.appsec.contrib_appsec.flask_app.app import redirect_requests
from tests.appsec.contrib_appsec.flask_app.app import service_renaming


cur_dir = os.path.dirname(os.path.realpath(__file__))
tmpl_path = os.path.join(cur_dir, "test_templates")


def _make_root_app():
    app = Flask("root_subapps", template_folder=tmpl_path)

    app.route("/", methods=["GET", "POST", "OPTIONS"])(index)
    # /asm (no trailing slash) is handled by DispatcherMiddleware — it matches the
    # /asm mount, hands the sub-app an empty PATH_INFO, and Werkzeug 308s to /asm/.
    # We can't bypass that from the root (a root-level /asm rule would be shadowed
    # by the mount before Flask ever saw it), so the endpoint collection exposes
    # /asm/ only and the test suite hits the trailing-slash URL.
    # Login endpoints stay on the root app: a sub-app mounted at "/login" would
    # make "/login" resolve against the sub-app with an empty PATH_INFO (Flask
    # rejects empty URL rules), while "/login/" would work — but the flat app
    # exposes "/login" without trailing slash and the test suite hits that URL.
    # Keeping login on the root sidesteps the conflict; the sub-app variant
    # still exercises DispatcherMiddleware on every other endpoint group.
    # Order matters on Werkzeug 1.x: when two rules share an endpoint (same view
    # function), the rule registered first "wins" the exact-match arbitration and
    # the slash-form gets slash-redirect treatment for requests without slash.
    # Register the no-slash form first to match the flat app's stacked-decorator
    # order (decorators apply bottom-up, so @route("/login") runs before
    # @route("/login/") in app.py).
    app.route("/login", methods=["GET"])(login_user)
    app.route("/login/", methods=["GET"])(login_user)
    app.route("/login_sdk", methods=["GET"])(login_user_sdk)
    app.route("/login_sdk/", methods=["GET"])(login_user_sdk)
    app.route("/exception-group-block", methods=["GET"])(exception_group_block)
    app.route("/buggy_endpoint/", methods=None)(buggy_endpoint)
    app.before_request(service_renaming)
    return app


def _make_asm_subapp():
    app = Flask("asm_sub", template_folder=tmpl_path)
    # Routes are attached directly on the sub-Flask-app rather than via a nested
    # Blueprint. A Blueprint-in-sub-app setup tripped Flask/Werkzeug 1.x routing
    # behavior (308 instead of 200 for the no-trailing-slash variant) and the
    # DispatcherMiddleware path is already the interesting thing to cover here;
    # nested-blueprint discovery has its own unit test in test_flask.py.
    # Register the no-slash form before the slash form to match flat app's
    # decorator order — Werkzeug 1.x arbitrates slash/no-slash matching by
    # registration order when two rules share an endpoint.
    app.route("/", methods=["GET", "POST", "OPTIONS"])(multi_view)
    app.route("/<int:param_int>/<string:param_str>", methods=["GET", "POST", "OPTIONS"])(multi_view)
    app.route("/<int:param_int>/<string:param_str>/", methods=["GET", "POST", "OPTIONS"])(multi_view)
    app.before_request(service_renaming)
    return app


def _make_new_service_subapp():
    app = Flask("new_service_sub", template_folder=tmpl_path)
    app.route("/<string:service_name>", methods=["GET", "POST", "OPTIONS"])(new_service)
    app.route("/<string:service_name>/", methods=["GET", "POST", "OPTIONS"])(new_service)
    app.before_request(service_renaming)
    return app


def _make_rasp_subapp():
    app = Flask("rasp_sub", template_folder=tmpl_path)
    app.route("/<string:endpoint>/", methods=["GET", "POST", "OPTIONS"])(rasp)
    app.before_request(service_renaming)
    return app


def _make_redirect_subapp(view, name):
    app = Flask(name, template_folder=tmpl_path)
    app.route("/<string:route>/<int:port>", methods=["GET", "POST"])(view)
    app.before_request(service_renaming)
    return app


root = _make_root_app()
asm_subapp = _make_asm_subapp()
new_service_subapp = _make_new_service_subapp()
rasp_subapp = _make_rasp_subapp()
redirect_subapp = _make_redirect_subapp(redirect, "redirect_sub")
redirect_requests_subapp = _make_redirect_subapp(redirect_requests, "redirect_requests_sub")
redirect_httpx_subapp = _make_redirect_subapp(redirect_httpx, "redirect_httpx_sub")
redirect_httpx_async_subapp = _make_redirect_subapp(redirect_httpx_async, "redirect_httpx_async_sub")


# Mount everything via DispatcherMiddleware assigned back to root.wsgi_app so that
# root.test_client() continues to work for the test fixture.
root.wsgi_app = DispatcherMiddleware(
    root.wsgi_app,
    {
        "/asm": asm_subapp,
        "/new_service": new_service_subapp,
        "/rasp": rasp_subapp,
        "/redirect": redirect_subapp,
        "/redirect_requests": redirect_requests_subapp,
        "/redirect_httpx": redirect_httpx_subapp,
        "/redirect_httpx_async": redirect_httpx_async_subapp,
    },
)

# Public handle used by the test fixture, same attribute name as in app.py.
app = root

# Keep the flat app importable so pytest collection still works.
__all__ = ("app", "flat_app")
