import falcon as falcon
from falcon import testing

from tests.utils import TracerTestCase

from .app import get_app
from .test_suite import FalconTestCase


class MiddlewareTestCase(TracerTestCase, testing.TestCase, FalconTestCase):
    """Executes tests using the manual instrumentation so a middleware
    is explicitly added.
    """

    def setUp(self):
        super(MiddlewareTestCase, self).setUp()

        # build a test app with a dummy tracer
        self._service = "falcon"
        self.api = get_app(tracer=self.tracer)
        self.version = falcon.__version__
        if self.version[0] != "1":
            self.client = testing.TestClient(self.api)
