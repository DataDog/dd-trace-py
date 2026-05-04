import importlib

from flask.testing import FlaskClient
import pytest

from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.internal.packages import get_version_for_package
from tests.appsec.contrib_appsec import utils
from tests.utils import TracerTestCase
from tests.utils import scoped_tracer


FLASK_VERSION = tuple(int(v) for v in get_version_for_package("flask").split("."))

_FLAT_APP = "tests.appsec.contrib_appsec.flask_app.app"
_SUBAPP_APP = "tests.appsec.contrib_appsec.flask_app.app_subapps"


class DDFlaskTestClient(FlaskClient):
    def __init__(self, *args, **kwargs):
        super(DDFlaskTestClient, self).__init__(*args, **kwargs)

    def open(self, *args, **kwargs):
        # From pep-333: If an iterable returned by the application has a close() method,
        # the server or gateway must call that method upon completion of the current request.
        # FlaskClient does not align with this specification so we must do this manually.
        # Closing the application iterable will finish the flask.request and flask.response
        # spans.
        res = super(DDFlaskTestClient, self).open(*args, **kwargs)
        res.make_sequence()
        if hasattr(res, "close"):
            # Note - werkzeug>=2.0 (used in flask>=2.0) calls response.close() for non streamed responses:
            # https://github.com/pallets/werkzeug/blame/b1911cd0a054f92fa83302cdb520d19449c0b87b/src/werkzeug/test.py#L1114
            res.close()
        return res


class BaseFlaskTestCase(TracerTestCase):
    app_module = _FLAT_APP

    def setUp(self):
        super(BaseFlaskTestCase, self).setUp()
        # Reload the selected variant so DM.__init__ re-fires under the currently-patched Flask/werkzeug.
        endpoint_collection.reset()
        module = importlib.reload(importlib.import_module(self.app_module))

        self.app = module.app
        self.app.test_client_class = DDFlaskTestClient
        self.client = self.app.test_client()

    def tearDown(self):
        super(BaseFlaskTestCase, self).tearDown()


class _Test_Flask_Base:
    """Flask-specific interface, response accessors, and argument parsing."""

    @pytest.fixture(params=[_FLAT_APP, _SUBAPP_APP], ids=["flat", "subapp"])
    def interface(self, printer, request):
        bftc = BaseFlaskTestCase()
        bftc.app_module = request.param

        bftc.setUp()
        bftc.app.config["SERVER_NAME"] = f"localhost:{self.SERVER_PORT}"
        interface = utils.Interface("flask", bftc.app, bftc.client)
        interface.version = FLASK_VERSION

        initial_get = bftc.client.get

        def patch_get(*args, **kwargs):
            if "cookies" in kwargs:
                for k, v in kwargs["cookies"].items():
                    if FLASK_VERSION < (2, 3, 0):
                        bftc.client.set_cookie(bftc.app.config["SERVER_NAME"], k, v)
                    else:
                        bftc.client.set_cookie(k, v)
                kwargs.pop("cookies")
            return initial_get(*args, **kwargs)

        bftc.client.get = patch_get

        initial_post = bftc.client.post

        def patch_post(*args, **kwargs):
            if "cookies" in kwargs:
                for k, v in kwargs["cookies"].items():
                    if FLASK_VERSION < (2, 3, 0):
                        bftc.client.set_cookie(bftc.app.config["SERVER_NAME"], k, v)
                    else:
                        bftc.client.set_cookie(k, v)
                kwargs.pop("cookies")
            return initial_post(*args, **kwargs)

        bftc.client.post = patch_post

        with scoped_tracer() as tracer:
            interface.tracer = tracer
            interface.printer = printer
            interface.SERVER_PORT = self.SERVER_PORT
            yield interface

        bftc.tearDown()

    def status(self, response):
        return response.status_code

    def headers(self, response):
        return response.headers

    def body(self, response):
        return response.data.decode("utf-8")

    def location(self, response):
        return response.location


@pytest.fixture
def _isolated_endpoints():
    """Hermetic endpoint_collection: reset on both sides so helper unit tests don't bleed state."""
    endpoint_collection.reset()
    yield
    endpoint_collection.reset()


class Test_Flask(_Test_Flask_Base, utils.Contrib_TestClass_For_Threats):
    # Paths both variants must expose. The subapp variant drops the flat app's bare /asm alias since the DM
    # mount shadows it; only /asm/ is reachable through the mount (serves the sub-app's "/" rule).
    ENDPOINT_DISCOVERY_EXPECTED_PATHS = {
        "/",
        "/asm/",
        "/asm/<int:param_int>/<string:param_str>",
        "/new_service/<string:service_name>",
        "/login",
        "/login_sdk",
        "/rasp/<string:endpoint>/",
    }

    @staticmethod
    def endpoint_path_to_uri(path: str) -> str:
        import re

        path = re.sub(r"<int:[a-z_]+>", "123", path)
        path = re.sub(r"<(str|string):[a-z_]+>", "abczx", path)
        return path

    # Helper unit tests live as methods on Test_Flask because the riot venv command pinpoints
    # ``::Test_Flask`` — module-level test_* functions would never be collected.

    def test_collect_flask_routes_registers_every_method_served(self, _isolated_endpoints):
        """Every method in ``rule.methods`` is registered — Werkzeug-auto-HEAD and Flask-auto-OPTIONS
        included — because anything yielding non-405 is part of the AppSec attack surface.
        """
        from flask import Flask

        from ddtrace.contrib.internal.flask.patch import _collect_flask_routes

        app = Flask("walk_test")

        @app.route("/users", methods=["GET", "POST"])
        def users():
            return "ok"

        @app.route("/items/<int:item_id>", methods=["GET"])
        def items(item_id):
            return "ok"

        _collect_flask_routes(app, "/api/v2")

        registered = {(ep.method, ep.path) for ep in endpoint_collection.endpoints}
        # User-declared methods.
        assert ("GET", "/api/v2/users") in registered
        assert ("POST", "/api/v2/users") in registered
        assert ("GET", "/api/v2/items/<int:item_id>") in registered
        # Werkzeug auto-adds HEAD for any rule containing GET; Flask auto-handles
        # OPTIONS for every rule. Both are now reported.
        assert ("HEAD", "/api/v2/users") in registered
        assert ("OPTIONS", "/api/v2/users") in registered
        assert ("HEAD", "/api/v2/items/<int:item_id>") in registered
        assert ("OPTIONS", "/api/v2/items/<int:item_id>") in registered

    def test_collect_flask_routes_normalizes_trailing_slash_in_script_name(self, _isolated_endpoints):
        """A SCRIPT_NAME ending in ``/`` must not produce ``//`` in registered paths."""
        from flask import Flask

        from ddtrace.contrib.internal.flask.patch import _collect_flask_routes

        app = Flask("trailing_slash_test")

        @app.route("/users", methods=["GET"])
        def users():
            return "ok"

        _collect_flask_routes(app, "/api/")

        registered = {(ep.method, ep.path) for ep in endpoint_collection.endpoints}
        assert ("GET", "/api/users") in registered
        assert ("GET", "/api//users") not in registered

    def test_collect_flask_routes_options_only_route(self, _isolated_endpoints):
        """``methods=["OPTIONS"]`` registers exactly OPTIONS — no phantom GET, no auto-HEAD
        (Werkzeug only auto-adds HEAD when GET is present).
        """
        from flask import Flask

        from ddtrace.contrib.internal.flask.patch import _collect_flask_routes

        app = Flask("options_only_test")

        @app.route("/options-only", methods=["OPTIONS"])
        def options_only():
            return "ok"

        _collect_flask_routes(app, "")

        registered = {(ep.method, ep.path) for ep in endpoint_collection.endpoints}
        assert ("OPTIONS", "/options-only") in registered
        assert ("GET", "/options-only") not in registered
        assert ("HEAD", "/options-only") not in registered

    @pytest.mark.skipif(FLASK_VERSION < (2, 0, 0), reason="Blueprint.register_blueprint added in Flask 2.0")
    def test_collect_flask_routes_registers_nested_blueprints(self, _isolated_endpoints):
        """Nested Blueprints — Flask prefix-joins in ``app.url_map``; the walker keeps the composed path."""
        from flask import Blueprint
        from flask import Flask

        from ddtrace.contrib.internal.flask.patch import _collect_flask_routes

        app = Flask("nested_bp")
        parent = Blueprint("parent", __name__, url_prefix="/parent")
        child = Blueprint("child", __name__, url_prefix="/child")

        @child.route("/item/<int:item_id>", methods=["GET"])
        def item(item_id):
            return "ok"

        parent.register_blueprint(child)
        app.register_blueprint(parent)

        _collect_flask_routes(app, "")

        registered = {(ep.method, ep.path) for ep in endpoint_collection.endpoints}
        assert ("GET", "/parent/child/item/<int:item_id>") in registered

    def test_walk_wsgi_mounts_recurses_through_dispatcher_middleware(self, _isolated_endpoints):
        """DispatcherMiddleware mounts must yield their Flask apps with composed prefixes."""
        from flask import Flask
        from werkzeug.middleware.dispatcher import DispatcherMiddleware

        from ddtrace.contrib.internal.flask.patch import _walk_wsgi_mounts

        main = Flask("main")
        inner = Flask("inner")
        outer_sub = Flask("outer_sub")

        # Nested DM: / -> main, /outer -> (inner default, /nested -> outer_sub)
        inner_dm = DispatcherMiddleware(inner, {"/nested": outer_sub})
        outer_dm = DispatcherMiddleware(main, {"/outer": inner_dm})

        found = {(app_.name, prefix) for app_, prefix in _walk_wsgi_mounts(outer_dm, "")}
        assert ("main", "") in found
        assert ("inner", "/outer") in found
        assert ("outer_sub", "/outer/nested") in found

    def test_late_add_url_rule_registers_under_known_script_names(self, _isolated_endpoints):
        """App-factory pattern: DM is built before a sub-app's routes are registered, and the sub-app then
        never receives a request. The eager DM-init walk runs against an empty ``url_map``; without re-walking
        from ``patched_add_url_rule``, late routes would never reach endpoint discovery for that sub-app.

        ``patched_add_url_rule`` re-runs ``_collect_flask_routes`` for ``instance`` under each known
        SCRIPT_NAME after the wrapped ``add_url_rule``, so the late route shows up immediately — no future
        request required.
        """
        from flask import Flask
        from werkzeug.middleware.dispatcher import DispatcherMiddleware

        main = Flask("main_factory")

        @main.route("/")
        def index():
            return "ok"

        api = Flask("api_factory")
        DispatcherMiddleware(main, {"/api": api})  # DM built before ``api`` has any routes.

        @api.route("/late", methods=["GET"])
        def late():
            return "ok"

        # No request anywhere — the late route must be registered purely by the re-walk in
        # ``patched_add_url_rule``.
        registered = {(ep.method, ep.path) for ep in endpoint_collection.endpoints}
        assert ("GET", "/api/late") in registered

    def test_dispatcher_middleware_init_eagerly_registers_unused_subapps(self, _isolated_endpoints):
        """DM.__init__ wrap registers sub-app routes at construction so they're discovered without traffic."""
        from flask import Flask
        from werkzeug.middleware.dispatcher import DispatcherMiddleware

        main = Flask("main_eager")

        @main.route("/")
        def index():
            return "ok"

        api = Flask("api_eager")

        @api.route("/never-called", methods=["GET"])
        def never_called():
            return "ok"

        DispatcherMiddleware(main, {"/v3": api})  # construction alone must populate endpoint_collection

        registered = {(ep.method, ep.path) for ep in endpoint_collection.endpoints}
        assert ("GET", "/") in registered
        assert ("GET", "/v3/never-called") in registered


class Test_Flask_RC(_Test_Flask_Base, utils.Contrib_TestClass_For_Threats_RC):
    pass
