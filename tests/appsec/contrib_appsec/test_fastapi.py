import fastapi
import httpx
import pytest
import starlette

from tests.appsec.contrib_appsec import utils
from tests.appsec.contrib_appsec.fastapi_app.app import get_app
from tests.appsec.contrib_appsec.fastapi_app.app_subapps import get_app_with_subapps
from tests.utils import scoped_tracer


FASTAPI_VERSION = tuple(int(v) for v in fastapi.__version__.split("."))
STARLETTE_VERSION = tuple(int(v) for v in starlette.__version__.split("."))
redirect_key = "allow_redirects" if STARLETTE_VERSION < (0, 21, 0) else "follow_redirects"
HTTPX_VERSION = tuple(int(v) for v in httpx.__version__.split("."))


class _Test_FastAPI_Base:
    """FastAPI-specific interface, response accessors, and argument parsing."""

    @pytest.fixture(params=[get_app, get_app_with_subapps], ids=["flat", "subapp"])
    def interface(self, printer, request):
        from fastapi.testclient import TestClient

        from ddtrace.internal.endpoints import endpoint_collection

        # Reset the singleton endpoint collection so each app variant starts clean
        endpoint_collection.reset()

        # for fastapi, test tracer needs to be set before the app is created
        # contrary to other frameworks
        with scoped_tracer() as tracer:
            application = request.param()

            client = TestClient(application, base_url="http://localhost:%d" % self.SERVER_PORT)

            # Patch the test client transport to preserve the original URL path
            # as raw_path in the ASGI scope. httpx resolves path traversal sequences
            # (e.g. /waf/../ -> /) before creating the Request object, so both
            # scope["path"] and scope["raw_path"] lose the original URI.
            # This stores the original path on the client and injects it into the
            # ASGI scope, matching what real ASGI servers (uvicorn) do.
            # Only available on newer Starlette/httpx versions that expose _transport.
            client._raw_path = None
            transport = getattr(client, "_transport", None)
            if transport is not None:
                original_handle = transport.handle_request

                def _handle_with_raw_path(request):
                    original_app = transport.app

                    async def app_with_raw_path(scope, receive, send):
                        if client._raw_path is not None and scope["type"] == "http":
                            scope["raw_path"] = client._raw_path.encode()
                        return await original_app(scope, receive, send)

                    transport.app = app_with_raw_path
                    try:
                        return original_handle(request)
                    finally:
                        transport.app = original_app

                transport.handle_request = _handle_with_raw_path

            def parse_arguments(*args, **kwargs):
                # Store the original URL path for raw_path injection
                if args:
                    client._raw_path = args[0] if isinstance(args[0], str) else None
                else:
                    client._raw_path = kwargs.get("url")
                if "content_type" in kwargs:
                    headers = kwargs.get("headers", {})
                    headers["Content-Type"] = kwargs["content_type"]
                    kwargs["headers"] = headers
                    del kwargs["content_type"]
                # httpx does not accept unicode headers and is now used in the TestClient
                if "headers" in kwargs and FASTAPI_VERSION >= (0, 87, 0):
                    kwargs["headers"] = {k.encode(): v.encode() for k, v in kwargs["headers"].items()}
                if HTTPX_VERSION >= (0, 18, 0) and STARLETTE_VERSION >= (0, 21, 0):
                    if "cookies" in kwargs:
                        client.cookies = kwargs["cookies"]
                        del kwargs["cookies"]
                    else:
                        client.cookies = {}
                    if "data" in kwargs and not isinstance(kwargs["data"], dict):
                        kwargs["content"] = kwargs["data"]
                        del kwargs["data"]
                if redirect_key not in kwargs:
                    kwargs[redirect_key] = False
                return args, kwargs

            initial_post = client.post

            def patch_post(*args, **kwargs):
                args, kwargs = parse_arguments(*args, **kwargs)
                return initial_post(*args, **kwargs)

            client.post = patch_post

            initial_get = client.get

            def patch_get(*args, **kwargs):
                args, kwargs = parse_arguments(*args, **kwargs)
                return initial_get(*args, **kwargs)

            client.get = patch_get

            interface = utils.Interface("fastapi", fastapi, client)
            interface.version = FASTAPI_VERSION
            interface.tracer = tracer
            interface.printer = printer
            interface.SERVER_PORT = self.SERVER_PORT
            yield interface

    def status(self, response):
        return response.status_code

    def headers(self, response):
        return response.headers

    def body(self, response):
        return response.text

    def location(self, response):
        return response.headers.get("location", "")


class Test_FastAPI(_Test_FastAPI_Base, utils.Contrib_TestClass_For_Threats):
    ENDPOINT_DISCOVERY_EXPECTED_PATHS = {
        "/",
        "/asm/{param_int:int}/{param_str:str}",
        "/asm/",
        "/new_service/{service_name:str}",
        "/login/",
        "/login_sdk/",
        "/rasp/{endpoint:str}/",
    }

    @staticmethod
    def endpoint_path_to_uri(path: str) -> str:
        import re

        path = re.sub(r"\{[a-z_]+:int\}", "123", path)
        path = re.sub(r"\{[a-z_]+:str\}", "abczx", path)
        return path


class Test_FastAPI_RC(_Test_FastAPI_Base, utils.Contrib_TestClass_For_Threats_RC):
    pass
