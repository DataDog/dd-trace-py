import fastapi
import httpx
import pytest
import starlette

from tests.appsec.contrib_appsec import utils
from tests.appsec.contrib_appsec.fastapi_app.app import get_app


FASTAPI_VERSION = tuple(int(v) for v in fastapi.__version__.split("."))
STARLETTE_VERSION = tuple(int(v) for v in starlette.__version__.split("."))
redirect_key = "allow_redirects" if STARLETTE_VERSION < (0, 21, 0) else "follow_redirects"
HTTPX_VERSION = tuple(int(v) for v in httpx.__version__.split("."))


class Test_FastAPI(utils.Contrib_TestClass_For_Threats):
    @pytest.fixture
    def interface(self, tracer, printer):
        from fastapi.testclient import TestClient

        # for fastapi, test tracer needs to be set before the app is created
        # contrary to other frameworks
        with utils.test_tracer() as tracer:
            application = get_app()

            client = TestClient(application, base_url="http://localhost:%d" % self.SERVER_PORT)

            def parse_arguments(*args, **kwargs):
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
            with utils.post_tracer(interface):
                yield interface

    def status(self, response):
        return response.status_code

    def headers(self, response):
        return response.headers

    def body(self, response):
        return response.text

    def location(self, response):
        return response.headers.get("location", "")
