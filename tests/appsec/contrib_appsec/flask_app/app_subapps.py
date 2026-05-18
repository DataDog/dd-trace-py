"""Sub-app variant of ``app.py``: views re-registered on sub-apps under DispatcherMiddleware."""

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
    # ``/login`` stays on root: mounting it would yield empty PATH_INFO (Flask rejects empty rules).
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
    # Direct routes (no Blueprint) — Blueprint-in-sub-app + DM hits a Werkzeug 1.x slash-redirect quirk.
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


# Assign DM back onto root.wsgi_app so root.test_client() still drives the dispatcher.
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

app = root
__all__ = ("app", "flat_app")
