import fastapi
import pytest

import ddtrace
from ddtrace.contrib.fastapi import patch as fastapi_patch
from ddtrace.contrib.fastapi import unpatch as fastapi_unpatch
from tests.appsec.contrib_appsec import utils
from tests.appsec.contrib_appsec.fastapi_app.app import get_app


FASTAPI_VERSION = tuple(int(v) for v in fastapi.__version__.split("."))


class Test_FastAPI(utils.Contrib_TestClass_For_Threats):
    @pytest.fixture
    def interface(self, tracer, printer):
        from fastapi.testclient import TestClient

        fastapi_patch()
        # for fastapi, test tracer needs to be set before the app is created
        # contrary to other frameworks
        with utils.test_tracer() as tracer:
            application = get_app()

            @application.middleware("http")
            async def traced_middlware(request, call_next):
                with ddtrace.tracer.trace("traced_middlware"):
                    response = await call_next(request)
                    return response

            client = TestClient(get_app(), base_url="http://localhost:%d" % self.SERVER_PORT)

            initial_post = client.post

            def patch_post(*args, **kwargs):
                if "content_type" in kwargs:
                    headers = kwargs.get("headers", {})
                    headers["Content-Type"] = kwargs["content_type"]
                    kwargs["headers"] = headers
                    del kwargs["content_type"]
                # httpx does not accept unicode headers and is now used in the TestClient
                if "headers" in kwargs and FASTAPI_VERSION >= (0, 87, 0):
                    kwargs["headers"] = {k.encode(): v.encode() for k, v in kwargs["headers"].items()}
                return initial_post(*args, **kwargs, allow_redirects=False)

            client.post = patch_post

            initial_get = client.get

            def patch_get(*args, **kwargs):
                if "content_type" in kwargs:
                    headers = kwargs.get("headers", {})
                    headers["Content-Type"] = kwargs["content_type"]
                    kwargs["headers"] = headers
                    del kwargs["content_type"]
                # httpx does not accept unicode headers and is now used in the TestClient
                if "headers" in kwargs and FASTAPI_VERSION >= (0, 87, 0):
                    kwargs["headers"] = {k.encode(): v.encode() for k, v in kwargs["headers"].items()}
                return initial_get(*args, **kwargs, allow_redirects=False)

            client.get = patch_get

            interface = utils.Interface("fastapi", fastapi, client)
            interface.tracer = tracer
            interface.printer = printer
            with utils.post_tracer(interface):
                yield interface
            fastapi_unpatch()

    def status(self, response):
        return response.status_code

    def headers(self, response):
        return response.headers

    def body(self, response):
        return response.text

    def location(self, response):
        return response.headers.get("location", "")
