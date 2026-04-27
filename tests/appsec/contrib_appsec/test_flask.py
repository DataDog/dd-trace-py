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
        # Reload the selected variant so DispatcherMiddleware.__init__ (in the subapp
        # variant) re-fires under the currently-patched Flask/werkzeug, repopulating
        # endpoint_collection with mount-prefixed paths after the reset below.
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
    """Hermetic endpoint_collection for the helper unit tests on Test_Flask.

    The _collect_flask_routes / _walk_wsgi_mounts tests poke the singleton
    endpoint_collection directly rather than going through the parametrized
    ``interface`` fixture. Reset on both sides of the yield so these tests
    can't bleed state into each other or into the full HTTP suites.
    """
    endpoint_collection.reset()
    yield
    endpoint_collection.reset()


class Test_Flask(_Test_Flask_Base, utils.Contrib_TestClass_For_Threats):
    # Paths both variants must expose with identical rule strings. The subapp variant
    # drops the flat app's bare "/asm" alias: DispatcherMiddleware intercepts the
    # prefix before a root-level rule could match, so only /asm/ is reachable through
    # the mount (the trailing-slash form serves the sub-app's "/" blueprint rule).
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

    # Helper-level unit tests below live as methods on Test_Flask (rather than at
    # module scope) because the riot venv command pinpoints `::Test_Flask`, so
    # any module-level test_* function would never get collected. They don't
    # request the `interface` fixture, so pytest does not parametrize them over
    # the flat/subapp matrix — they run exactly once each.

    def test_collect_flask_routes_prefixes_script_name(self, _isolated_endpoints):
        """_collect_flask_routes must prepend the script_name to every rule."""
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
        assert ("GET", "/api/v2/users") in registered
        assert ("POST", "/api/v2/users") in registered
        assert ("GET", "/api/v2/items/<int:item_id>") in registered
        # Auto-added HEAD/OPTIONS must be stripped to match the user-declared method surface.
        assert ("HEAD", "/api/v2/users") not in registered
        assert ("OPTIONS", "/api/v2/users") not in registered

    @pytest.mark.skipif(FLASK_VERSION < (2, 0, 0), reason="Blueprint.register_blueprint added in Flask 2.0")
    def test_collect_flask_routes_registers_nested_blueprints(self, _isolated_endpoints):
        """Nested Blueprint rules are already prefix-joined by Flask itself in
        ``app.url_map``; the walker must pick them up with the full composed path.
        """
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

    def test_dispatcher_middleware_init_eagerly_registers_unused_subapps(self, _isolated_endpoints):
        """The DispatcherMiddleware.__init__ wrap must register sub-app routes at
        construction time, so endpoint discovery finds them even when they never
        receive a request (Pattern B parity with Option II).
        """
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

        # Construction alone (no request) must populate endpoint_collection.
        DispatcherMiddleware(main, {"/v3": api})

        registered = {(ep.method, ep.path) for ep in endpoint_collection.endpoints}
        assert ("GET", "/") in registered
        assert ("GET", "/v3/never-called") in registered


class Test_Flask_RC(_Test_Flask_Base, utils.Contrib_TestClass_For_Threats_RC):
    pass
