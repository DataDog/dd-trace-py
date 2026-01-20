import pytest
from tornado.testing import AsyncHTTPTestCase

from ddtrace.internal.packages import get_version_for_package
from tests.appsec.contrib_appsec import utils
from tests.appsec.contrib_appsec.tornado_app.app import get_app as tornado_get_app
from tests.utils import scoped_tracer


TORNADO_VERSION = tuple(int(v) for v in get_version_for_package("tornado").split("."))

base_url = "http://localhost:%d"


class TornadoTestClient(AsyncHTTPTestCase):
    def get_app(self):
        self._app = tornado_get_app()
        return self._app


def wrap_fetch(original_fetch, ttc, interface, **fetch_kwargs):
    def wrapped_fetch(request, *args, **kwargs):
        try:
            ttc.setUp()
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
                (base_url % interface.SERVER_PORT) + request, *args, raise_error=False, **(fetch_kwargs | kwargs)
            )
            loop.run_sync(lambda: future)
            res = future.result()
            return res
        finally:
            ttc.tearDown()

    return wrapped_fetch


class Test_Tornado(utils.Contrib_TestClass_For_Threats):
    @pytest.fixture
    def interface(self, printer):
        ttc = TornadoTestClient()
        app = ttc.get_app()
        interface = utils.Interface("tornado", app, ttc.get_http_client())
        interface.version = TORNADO_VERSION

        interface.client.get = wrap_fetch(interface.client.fetch, ttc, interface)
        interface.client.post = wrap_fetch(interface.client.fetch, ttc, interface, method="POST")
        interface.client.options = wrap_fetch(interface.client.fetch, ttc, interface, method="OPTIONS")

        with scoped_tracer() as tracer:
            interface.tracer = tracer
            interface.printer = printer
            with utils.post_tracer(interface):
                yield interface

    def status(self, response):
        return response.code

    def headers(self, response):
        res = {key.lower(): val for key, val in response.headers.items()}
        return res

    def body(self, response):
        return response.body.decode("utf-8")

    def location(self, response):
        return response.location
