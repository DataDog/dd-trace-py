import pytest

import ddtrace
from ddtrace.contrib.fastapi import patch as fastapi_patch
from ddtrace.contrib.fastapi import unpatch as fastapi_unpatch
from tests.appsec.contrib_appsec import utils
from tests.appsec.contrib_appsec.fastapi_app.app import get_app


class Test_FastAPI(utils.Contrib_TestClass_For_Threats):
    @pytest.fixture
    def interface(self, tracer):
        import fastapi
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
            interface = utils.Interface("fastapi", fastapi, client)
            interface.tracer = tracer
            with utils.post_tracer(interface):
                yield interface
            fastapi_unpatch()

    def status(self, response):
        return response.status_code

    def headers(self, response):
        return response.headers

    def body(self, response):
        return response.text
