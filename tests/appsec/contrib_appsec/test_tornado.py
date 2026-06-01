import pytest
from tornado.testing import AsyncHTTPTestCase

from ddtrace.appsec import _constants as asm_constants
from ddtrace.internal.packages import get_version_for_package
from tests.appsec.contrib_appsec import utils
from tests.appsec.contrib_appsec.tornado_app.app import get_app as tornado_get_app
from tests.utils import override_global_config
from tests.utils import scoped_tracer


TORNADO_VERSION = tuple(int(v) for v in get_version_for_package("tornado").split("."))

base_url = "http://localhost:%d"


class TornadoTestClient(AsyncHTTPTestCase):
    def get_app(self):
        self._app = tornado_get_app()
        return self._app


def wrap_fetch(original_fetch, ttc, interface, **fetch_kwargs):
    def wrapped_fetch(request, *args, **kwargs):
        loop = ttc.io_loop
        if "data" in kwargs:
            body = kwargs.pop("data")
            if isinstance(body, str):
                body = body.encode("utf-8")
            elif isinstance(body, dict):
                import urllib.parse

                body = urllib.parse.urlencode(body).encode("utf-8")
            kwargs["body"] = body
        if "content_type" in kwargs:
            if "headers" not in kwargs:
                kwargs["headers"] = {}
            kwargs["headers"]["Content-Type"] = kwargs.pop("content_type")
        if "cookies" in kwargs:
            cookies = kwargs.pop("cookies")
            cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
            if "headers" not in kwargs:
                kwargs["headers"] = {}
            kwargs["headers"]["Cookie"] = cookie_header
        interface.SERVER_PORT = ttc.get_http_port()
        future = original_fetch(
            (base_url % interface.SERVER_PORT) + request,
            *args,
            max_redirects=0,
            raise_error=False,
            **(fetch_kwargs | kwargs),
        )
        loop.run_sync(lambda: future)
        res = future.result()
        return res

    return wrapped_fetch


class _Test_Tornado_Base:
    """Tornado-specific interface, response accessors, and argument parsing."""

    @pytest.fixture
    def interface(self, printer):
        ttc = TornadoTestClient()
        interface = utils.Interface("tornado", None, ttc.get_http_client())
        interface.version = TORNADO_VERSION

        interface.client.get = wrap_fetch(interface.client.fetch, ttc, interface)
        interface.client.post = wrap_fetch(interface.client.fetch, ttc, interface, method="POST")
        interface.client.options = wrap_fetch(interface.client.fetch, ttc, interface, method="OPTIONS")

        with scoped_tracer() as tracer:
            ttc.setUp()
            interface.tracer = tracer
            interface.printer = printer
            yield interface

    def status(self, response):
        return response.code

    def headers(self, response):
        res = {key.lower(): val for key, val in response.headers.items()}
        return res

    def body(self, response):
        return response.body.decode("utf-8")

    def location(self, response):
        return self.headers(response)["location"]


class Test_Tornado(_Test_Tornado_Base, utils.Contrib_TestClass_For_Threats):
    ENDPOINT_DISCOVERY_EXPECTED_PATHS = {
        "/",
        "/asm/%s/%s/?",
        "/asm/?",
        "/new_service/%s/?",
        "/login/?",
        "/login_sdk/?",
        "/rasp/%s/?",
        "/multi-param/%s.%s/?",
        "/files/%s",
    }

    @staticmethod
    def endpoint_path_to_uri(path: str) -> str:
        # Tornado uses %s for all capturing groups and does not have a notion of typed path parameters.
        # We substitute with "123" which satisfies both \d+ and [^/]+ patterns.
        path = path.replace("%s", "123")
        if path.endswith("/?"):
            path = path[:-2]
        return path if path.startswith("/") else ("/" + path)

    # Tornado's routes use ``/?`` for optional trailing slash, which per RFC-1103 rule 1 is NOT
    # "declared with a trailing slash". The normalizer strips ``/?`` and emits no trailing slash
    # even when the request URL had one. Hence the expected values differ from other frameworks.
    @pytest.mark.parametrize("asm_enabled", [True, False])
    @pytest.mark.parametrize(
        ("uri", "expected"),
        [
            # ``/?`` route: optional slash is not declared → no trailing slash in normalized form.
            ("/asm/137/abc/", "/asm/{param_int}/{param_str}"),
            ("/asm/137/abc", "/asm/{param_int}/{param_str}"),
            ("/", "/"),
            # Multi-param segment: two named groups combined with ``+`` (rule 5). ``/?`` → no trailing slash.
            ("/multi-param/john.doe/", "/multi-param/{first+last}"),
            # Catch-all: single ``%s`` spanning multiple URL segments (rule 5 catch-all exception).
            ("/files/some/deep/path", "/files/{file_path}"),
        ],
    )
    def test_normalized_route(self, interface: utils.Interface, get_entry_span_tag, asm_enabled, uri, expected):
        with override_global_config(dict(_asm_enabled=asm_enabled)):
            self.update_tracer(interface)
            response = interface.client.get(uri)
            assert self.status(response) == 200
            tag = get_entry_span_tag(asm_constants.API_SECURITY.NORMALIZED_ROUTE)
            if asm_enabled:
                assert tag == expected, f"normalized_route tag mismatch: {tag!r} != {expected!r}"
            else:
                assert tag is None, f"normalized_route should be unset when ASM is disabled, got {tag!r}"

    def test_normalized_route_disabled_when_api_security_off(self, interface: utils.Interface, get_entry_span_tag):
        with override_global_config(dict(_asm_enabled=True, _api_security_enabled=False)):
            self.update_tracer(interface)
            response = interface.client.get("/asm/137/abc/")
            assert self.status(response) == 200
            tag = get_entry_span_tag(asm_constants.API_SECURITY.NORMALIZED_ROUTE)
            assert tag is None, f"normalized_route should be unset when API Security is disabled, got {tag!r}"


class Test_Tornado_RC(_Test_Tornado_Base, utils.Contrib_TestClass_For_Threats_RC):
    pass
