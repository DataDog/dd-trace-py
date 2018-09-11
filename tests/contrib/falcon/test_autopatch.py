from ddtrace import tracer
from tests.test_tracer import DummyWriter

from falcon import testing
from falcon.testing.client import TestClient

from .app import get_app
from .test_suite import FalconTestCase


class AutoPatchTestCase(testing.TestCase, FalconTestCase):

    def __init__(self, methodName='runTest'):
        super(AutoPatchTestCase, self).__init__(methodName=methodName)
        # Required as in falcon 1.3+ some required test properties are initialized
        # in the __init__ method
        TestClient.__init__(self, None)

    def setUp(self):
        self._service = 'my-falcon'
        self.tracer = tracer
        self.tracer.writer = DummyWriter()

        # build a test app without adding a tracer middleware;
        # reconfigure the global tracer since the autopatch mode
        # uses it
        self.api = get_app(tracer=None)
