from tornado.testing import AsyncHTTPTestCase
import pytest

from ddtrace._trace.pin import Pin
from ddtrace.internal.packages import get_version_for_package
from tests.appsec.contrib_appsec import utils
from tests.utils import TracerTestCase
from tests.utils import scoped_tracer
from tests.appsec.contrib_appsec.tornado_app.app import get_app


TORNADO_VERSION = tuple(int(v) for v in get_version_for_package("tornado").split("."))

class TornadoTestClient(AsyncHTTPTestCase):

    def get_app(self):
        self._app = get_app()
        return self._app

class Test_Tornado(utils.Contrib_TestClass_For_Threats):
    @pytest.fixture
    def interface(self, printer):
        ttc = TornadoTestClient()
        interface = utils.Interface("flask", ttc._app, ttc.http_client)
        interface.version = TORNADO_VERSION

        with scoped_tracer() as tracer:
            interface.tracer = tracer
            interface.printer = printer
            with utils.post_tracer(interface):
                yield interface


    def status(self, response):
        return response.status_code

    def headers(self, response):
        return response.headers

    def body(self, response):
        return response.data.decode("utf-8")

    def location(self, response):
        return response.location
