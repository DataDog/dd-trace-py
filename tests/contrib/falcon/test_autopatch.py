from ddtrace import tracer
from tests.test_tracer import DummyWriter

from falcon import testing

from .app import get_app
from .test_suite import FalconTestCase


class AutoPatchTestCase(testing.TestCase, FalconTestCase):
    def setUp(self):
        super(AutoPatchTestCase, self).setUp()

        # build a test app without adding a tracer middleware;
        # reconfigure the global tracer since the autopatch mode
        # uses it
        self._service = 'my-falcon'
        self.tracer = tracer
        self.tracer.writer = DummyWriter()
        self.api = get_app(tracer=None)
