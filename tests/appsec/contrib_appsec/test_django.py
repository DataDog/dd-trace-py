import importlib
import os

import django
from django.conf import settings
from django.test.client import Client
from django.urls import clear_url_caches
import pytest

from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.propagation._utils import get_wsgi_header
from tests.appsec.contrib_appsec import utils
from tests.utils import scoped_tracer


_FLAT_URLCONF = "tests.appsec.contrib_appsec.django_app.urls"
_SUBAPP_URLCONF = "tests.appsec.contrib_appsec.django_app.urls_subapps"


class _Test_Django_Base:
    """Django-specific interface, response accessors, and argument parsing."""

    @pytest.fixture(params=[_FLAT_URLCONF, _SUBAPP_URLCONF], ids=["flat", "subapp"])
    def interface(self, printer, request):
        os.environ["DJANGO_SETTINGS_MODULE"] = "tests.appsec.contrib_appsec.django_app.settings"
        settings.DEBUG = False
        django.setup()

        # Pre-import both urlconfs so their module-level side effects (urls.py
        # opens an in-memory sqlite connection, urls_subapps.py imports views
        # from urls.py) run once, independent of which variant the fixture
        # selects first. Endpoint registration is now lazy (first-request walk),
        # so these imports no longer publish anything into endpoint_collection.
        importlib.import_module(_FLAT_URLCONF)
        importlib.import_module(_SUBAPP_URLCONF)

        # Switch ROOT_URLCONF for this variant, clear Django's URL resolver cache,
        # and reset the singleton endpoint collection. The reload rebuilds the
        # module's urlpatterns with the current traced_urls_path wrapping so
        # lifecycle/CBV instrumentation is re-applied; the actual endpoint
        # registration happens lazily on the first request, when _collect_routes_once
        # walks the fresh resolver returned by get_resolver() post-clear.
        settings.ROOT_URLCONF = request.param
        clear_url_caches()
        endpoint_collection.reset()
        importlib.reload(importlib.import_module(request.param))

        client = Client(
            f"http://localhost:{self.SERVER_PORT}",
            SERVER_NAME=f"localhost:{self.SERVER_PORT}",
        )
        initial_get = client.get

        def patch_get(*args, **kwargs):
            headers = {}
            if "cookies" in kwargs:
                client.cookies.load(kwargs["cookies"])
                kwargs.pop("cookies")
            if "headers" in kwargs:
                headers = kwargs["headers"]
                kwargs.pop("headers")
            # https://docs.djangoproject.com/en/5.0/ref/request-response/#:~:text=With%20the%20exception%20of%20CONTENT_LENGTH,HTTP_%20prefix%20to%20the%20name
            # test client does not add HTTP_ prefix to headers like a real Django server would
            meta_headers = {}
            for k, v in headers.items():
                meta_headers[get_wsgi_header(k)] = v
            return initial_get(*args, **kwargs, **meta_headers)

        client.get = patch_get

        initial_post = client.post

        def patch_post(*args, **kwargs):
            headers = {}
            if "cookies" in kwargs:
                client.cookies.load(kwargs["cookies"])
                kwargs.pop("cookies")
            if "headers" in kwargs:
                headers = kwargs["headers"]
                kwargs.pop("headers")
            # https://docs.djangoproject.com/en/5.0/ref/request-response/#:~:text=With%20the%20exception%20of%20CONTENT_LENGTH,HTTP_%20prefix%20to%20the%20name
            # test client does not add HTTP_ prefix to headers like a real Django server would
            meta_headers = {}
            for k, v in headers.items():
                meta_headers[get_wsgi_header(k)] = v
            return initial_post(*args, **kwargs, **meta_headers)

        client.post = patch_post

        interface = utils.Interface("django", django, client)
        interface.SERVER_PORT = self.SERVER_PORT
        interface.version = django.VERSION
        with scoped_tracer() as tracer:
            interface.tracer = tracer
            interface.printer = printer
            yield interface

    def status(self, response):
        return response.status_code

    def headers(self, response):
        if django.VERSION >= (3, 0, 0):
            return response.headers
        # Django 2.x
        return {k: v[1] for k, v in response._headers.items()}

    def body(self, response):
        return response.content.decode("utf-8")

    def location(self, response):
        return response["location"]


class Test_Django(_Test_Django_Base, utils.Contrib_TestClass_For_Threats):
    # Paths registered in BOTH the flat urlconf and the sub-application urlconf
    # (once Django sub-app route discovery is wired up correctly). Entries whose
    # raw pattern string differs across variants cannot appear in a shared set —
    # flat registers regex "^asm/?$" and bare "login"/"login_sdk", while subapp
    # registers plain "asm"/"login"/"login_sdk" plus the include()-joined forms
    # "asm/<...>", "login/", "login_sdk/". Each variant's variant-specific paths
    # are still request-checked in test_api_endpoint_discovery, which iterates
    # every collected endpoint and hits its URI regardless of this set.
    ENDPOINT_DISCOVERY_EXPECTED_PATHS = {
        "^$",
        "asm/<int:param_int>/<str:param_str>",
        "new_service/<str:service_name>",
        "rasp/<str:endpoint>",
    }

    @staticmethod
    def endpoint_path_to_uri(path: str) -> str:
        import re

        # Django regex-style routes: ^asm/?$ → /asm
        if re.match(r"^\^.*\$$", path):
            path = path[1:-1]
        if path.endswith("/?"):
            path = path[:-2]
        path = re.sub(r"<int:[a-z_]+>", "123", path)
        path = re.sub(r"<str:[a-z_]+>", "abczx", path)
        return path if path.startswith("/") else ("/" + path)


class Test_Django_RC(_Test_Django_Base, utils.Contrib_TestClass_For_Threats_RC):
    pass


@pytest.fixture(scope="module")
def _django_ready():
    os.environ["DJANGO_SETTINGS_MODULE"] = "tests.appsec.contrib_appsec.django_app.settings"
    django.setup()


@pytest.fixture
def _isolated_endpoints(_django_ready):
    """Hermetic endpoint_collection for module-level unit tests.

    The _collect_django_routes tests call into the singleton endpoint_collection
    directly rather than going through the `interface` fixture (which handles
    reset itself for the full HTTP test classes). Reset on both sides of the
    yield so these tests can't bleed state into each other or into whichever
    test runs next in this module.
    """
    endpoint_collection.reset()
    yield
    endpoint_collection.reset()


def test_collect_django_routes_joins_regex_children_like_join_route(_isolated_endpoints):
    """re_path children under any include() must be joined without the child's leading ^.

    This mirrors django.urls.resolvers.URLResolver._join_route, which is what
    Django itself uses to build request.resolver_match.route. A naive concat
    would register `^api/^users/$` rather than `^api/users/$`.
    """
    from django.urls import include
    from django.urls import re_path

    from ddtrace.contrib.internal.django.patch import _collect_django_routes

    def users_view(request):
        return None

    def status_view(request):
        return None

    patterns = [
        re_path(
            r"^api/",
            include(
                [
                    re_path(r"^users/$", users_view, name="users"),
                    re_path(r"^status$", status_view, name="status"),
                ]
            ),
        ),
    ]
    _collect_django_routes(patterns)

    registered = {ep.path for ep in endpoint_collection.endpoints}
    assert "^api/users/$" in registered
    assert "^api/status$" in registered
    assert "^api/^users/$" not in registered
    assert "^api/^status$" not in registered


def test_collect_django_routes_handles_mixed_path_and_re_path(_isolated_endpoints):
    """path() prefix + re_path() child (and vice versa) must still produce a joined route."""
    from django.urls import include
    from django.urls import path
    from django.urls import re_path

    from ddtrace.contrib.internal.django.patch import _collect_django_routes

    def a(request):
        return None

    def b(request):
        return None

    patterns = [
        path("v1/", include([re_path(r"^ping$", a, name="ping")])),
        re_path(r"^v2/", include([path("pong", b, name="pong")])),
    ]
    _collect_django_routes(patterns)

    registered = {ep.path for ep in endpoint_collection.endpoints}
    assert "v1/ping$" in registered
    assert "^v2/pong" in registered


def test_collect_pattern_methods_respects_require_http_methods(_django_ready):
    """@require_http_methods must survive @csrf_exempt wrapping and produce the method list."""
    from django.views.decorators.csrf import csrf_exempt
    from django.views.decorators.http import require_http_methods

    from ddtrace.contrib.internal.django.patch import _collect_pattern_methods

    @csrf_exempt
    @require_http_methods(["GET", "POST", "OPTIONS"])
    def view(request):
        return None

    methods = _collect_pattern_methods(view)
    assert set(methods) == {"GET", "POST", "OPTIONS"}


def test_collect_pattern_methods_undecorated_view_falls_back_to_wildcard(_django_ready):
    """A plain view with no method restriction and no http_method_names is tagged as '*'."""
    from ddtrace.contrib.internal.django.patch import _collect_pattern_methods

    def view(request):
        return None

    assert _collect_pattern_methods(view) == ["*"]
