from ddtrace import tracer
from tests.test_tracer import DummyWriter

from falcon import testing

from .app import get_app
from .test_suite import FalconTestCase


class AutoPatchTestCase(testing.TestCase, FalconTestCase):

    # Added because falcon 1.3 and 1.4 test clients (falcon.testing.client.TestClient) expect this property to be
    # defined. It would be initialized in the constructor, but we call it here like in 'TestClient.__init__(self, None)'
    # because falcon 1.0.x does not have such module and would fail. Once we stop supporting falcon 1.0.x then we can
    # use the cleaner __init__ invocation
    _default_headers = None

    def setUp(self):
        self._service = 'my-falcon'
        self.tracer = tracer
        self.tracer.writer = DummyWriter()

        # build a test app without adding a tracer middleware;
        # reconfigure the global tracer since the autopatch mode
        # uses it
        self.api = get_app(tracer=None)
