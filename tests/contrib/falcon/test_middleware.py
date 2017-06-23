from falcon import testing
from tests.test_tracer import get_dummy_tracer

from .app import get_app
from .test_suite import FalconTestCase


class MiddlewareTestCase(testing.TestCase, FalconTestCase):
    """Executes tests using the manual instrumentation so a middleware
    is explicitly added.
    """
    def setUp(self):
        super(MiddlewareTestCase, self).setUp()

        # build a test app with a dummy tracer
        self._service = 'falcon'
        self.tracer = get_dummy_tracer()
        self.api = get_app(tracer=self.tracer)
