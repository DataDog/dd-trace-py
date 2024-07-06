from flask.testing import FlaskClient
import pytest

from ddtrace import Pin
from ddtrace.internal.packages import get_version_for_package
from tests.appsec.contrib_appsec import utils
from tests.utils import TracerTestCase


FLASK_VERSION = tuple(int(v) for v in get_version_for_package("flask").split("."))


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
    def setUp(self):
        super(BaseFlaskTestCase, self).setUp()
        from tests.appsec.contrib_appsec.flask_app.app import app

        self.app = app
        self.app.test_client_class = DDFlaskTestClient
        self.client = self.app.test_client()
        Pin.override(self.app, tracer=self.tracer)

    def tearDown(self):
        super(BaseFlaskTestCase, self).tearDown()


class Test_Flask(utils.Contrib_TestClass_For_Threats):
    @pytest.fixture
    def interface(self, printer):
        bftc = BaseFlaskTestCase()

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

        with utils.test_tracer() as tracer:
            interface.tracer = tracer
            interface.printer = printer
            with utils.post_tracer(interface):
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
