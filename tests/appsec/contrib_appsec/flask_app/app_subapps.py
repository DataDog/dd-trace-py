"""Sub-application variant of the Flask test app.

Views from ``app.py`` are re-registered on per-prefix sub-Flask-apps mounted under
``werkzeug.middleware.dispatcher.DispatcherMiddleware`` to exercise the DM code paths
(endpoint discovery, span resource with script_root, etc.).
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
    # ``/asm`` (no slash) is owned by the DM mount — a root-level rule would be shadowed; only ``/asm/`` is
    # reachable. ``/login`` stays on root because mounting it would yield empty PATH_INFO (Flask rejects
    # empty URL rules) and the test suite hits the bare ``/login``. No-slash form first to match the flat
    # app's stacked-decorator order — Werkzeug 1.x arbitrates slash/no-slash by registration order.
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
    # Direct app routes (no Blueprint) — Blueprint-in-sub-app + DM hit a Werkzeug 1.x slash-redirect quirk;
    # nested-blueprint discovery has its own unit test in test_flask.py.
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


# Mount via DM assigned back to root.wsgi_app so root.test_client() still works.
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
